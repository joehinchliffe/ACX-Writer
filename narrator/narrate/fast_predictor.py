"""Drop-in replacement for ``talker.code_predictor.generate`` in qwen-tts.

The stock library predicts the 15 residual codebook tokens of every audio frame
with Hugging Face ``generate()``. The predictor is a tiny 5-layer transformer,
so the per-step Python/``generate`` overhead dominates. This module runs the
same maths by hand:

* a preallocated KV cache of ``num_code_groups`` positions (16 for 12 Hz),
* direct calls to the layer modules (projections, q/k RMSNorm, RoPE,
  SDPA with GQA, SwiGLU MLP),
* temperature + top-k sampling on the GPU with the exponential-race trick
  (``argmax(p / E)``, E ~ Exp(1)), which has the same distribution as
  ``torch.multinomial(p)``,
* on CUDA, the whole 15-step loop captured as ONE CUDA graph per
  (batch size, sampling settings).

Every op mirrors the shapes the library uses (prefill of 2 positions, then
1 position per step, logits of all positions then the last one cast to
float32), so with greedy decoding the codes are bit-identical to the library;
``bench/exactness.py`` and ``tests/test_fast_predictor.py`` check this.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Dict, Optional, Tuple

import torch
import torch.nn.functional as F


def _rotate_half(x: torch.Tensor) -> torch.Tensor:
    x1 = x[..., : x.shape[-1] // 2]
    x2 = x[..., x.shape[-1] // 2:]
    return torch.cat((-x2, x1), dim=-1)


class FastCodePredictor:
    def __init__(self, code_predictor, use_cuda_graph: Optional[bool] = None,
                 contiguous_kv: bool = False):
        self.cp = code_predictor
        self.model = code_predictor.model
        cfg = code_predictor.config
        self.cfg = cfg
        self.n_groups = cfg.num_code_groups
        self.n_steps = self.n_groups - 1                     # 15 for the 12 Hz tokenizer
        self.n_pos = self.n_groups                           # 2 prefill + 14 decode positions
        self.n_layers = cfg.num_hidden_layers
        self.n_heads = cfg.num_attention_heads
        self.n_kv = cfg.num_key_value_heads
        self.head_dim = getattr(cfg, "head_dim", cfg.hidden_size // cfg.num_attention_heads)
        self.scaling = self.head_dim ** -0.5
        self.contiguous_kv = contiguous_kv
        layer_types = getattr(cfg, "layer_types", None) or ["full_attention"] * self.n_layers
        if any(t != "full_attention" for t in layer_types[: self.n_layers]):
            raise NotImplementedError("sliding-window layers in the code predictor are not supported")
        p = next(code_predictor.parameters())
        self.device, self.dtype = p.device, p.dtype
        if use_cuda_graph is None:
            use_cuda_graph = self.device.type == "cuda"
        self.use_cuda_graph = use_cuda_graph
        # RoPE tables for positions 0..n_pos-1, computed by the model's own rotary module
        # (elementwise per position, so identical to what the library computes per step).
        dummy = torch.zeros(1, 1, device=self.device, dtype=self.dtype)
        pos = torch.arange(self.n_pos, device=self.device).unsqueeze(0)
        with torch.inference_mode():
            cos, sin = self.model.rotary_emb(dummy, pos)
        self.cos, self.sin = cos.clone(), sin.clone()        # (1, n_pos, head_dim)
        self._graphs: Dict[Tuple, SimpleNamespace] = {}
        self._pool = None
        self.calls = 0

    # ------------------------------------------------------------------ maths
    def _layers_forward(self, x, start: int, kc, vc):
        """x: (B, L, hidden) at positions start..start+L-1. Updates caches in place."""
        B, L, _ = x.shape
        end = start + L
        cos = self.cos[:, start:end].unsqueeze(1)
        sin = self.sin[:, start:end].unsqueeze(1)
        for i, layer in enumerate(self.model.layers[: self.n_layers]):
            attn = layer.self_attn
            residual = x
            h = layer.input_layernorm(x)
            q = attn.q_norm(attn.q_proj(h).view(B, L, -1, self.head_dim)).transpose(1, 2)
            k = attn.k_norm(attn.k_proj(h).view(B, L, -1, self.head_dim)).transpose(1, 2)
            v = attn.v_proj(h).view(B, L, -1, self.head_dim).transpose(1, 2)
            q = (q * cos) + (_rotate_half(q) * sin)
            k = (k * cos) + (_rotate_half(k) * sin)
            kc[i][:, :, start:end] = k
            vc[i][:, :, start:end] = v
            K = kc[i][:, :, :end]
            V = vc[i][:, :, :end]
            if self.contiguous_kv:
                K, V = K.contiguous(), V.contiguous()
            o = F.scaled_dot_product_attention(q, K, V, dropout_p=0.0, scale=self.scaling,
                                               is_causal=L > 1, enable_gqa=True)
            o = o.transpose(1, 2).contiguous().reshape(B, L, -1).contiguous()
            x = residual + attn.o_proj(o)
            residual = x
            x = residual + layer.mlp(layer.post_attention_layernorm(x))
        return self.model.norm(x)

    def _pick(self, logits, do_sample: bool, top_k: int, temperature: float):
        if not do_sample:
            return torch.argmax(logits, dim=-1)
        scores = logits / temperature
        if top_k and top_k > 0:
            k = min(top_k, scores.size(-1))
            kth = torch.topk(scores, k)[0][..., -1, None]
            scores = scores.masked_fill(scores < kth, float("-inf"))
        probs = torch.softmax(scores, dim=-1)
        # argmax(p_i / E_i), E_i ~ Exp(1): P(i wins) = p_i / sum(p) -- same law as multinomial.
        e = torch.empty_like(probs).exponential_()
        return torch.argmax(probs / e, dim=-1)

    def _run(self, inputs_embeds, kc, vc, do_sample, top_k, temperature):
        """inputs_embeds: (B, 2, talker_hidden). Returns (B, n_steps) int64."""
        cp = self.cp
        x = cp.small_to_mtp_projection(inputs_embeds)
        h = self._layers_forward(x, 0, kc, vc)
        logits = cp.lm_head[0](h)[:, -1, :].to(torch.float32)
        tok = self._pick(logits, do_sample, top_k, temperature)
        toks = [tok]
        emb = self.model.codec_embedding
        for g in range(1, self.n_steps):
            x = cp.small_to_mtp_projection(emb[g - 1](tok.unsqueeze(1)))
            h = self._layers_forward(x, g + 1, kc, vc)
            logits = cp.lm_head[g](h)[:, -1, :].to(torch.float32)
            tok = self._pick(logits, do_sample, top_k, temperature)
            toks.append(tok)
        return torch.stack(toks, dim=1)

    def _new_cache(self, B):
        shape = (B, self.n_kv, self.n_pos, self.head_dim)
        mk = lambda: [torch.zeros(shape, device=self.device, dtype=self.dtype) for _ in range(self.n_layers)]
        return mk(), mk()

    # --------------------------------------------------------------- graphing
    def _graph_for(self, B, H, do_sample, top_k, temperature):
        key = (B, H, bool(do_sample), int(top_k or 0), float(temperature))
        g = self._graphs.get(key)
        if g is not None:
            return g
        static_in = torch.zeros(B, 2, H, device=self.device, dtype=self.dtype)
        kc, vc = self._new_cache(B)
        s = torch.cuda.Stream()
        s.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(s):
            for _ in range(2):                                # warm-up (cuBLAS handles, kernels)
                self._run(static_in, kc, vc, do_sample, top_k, temperature)
        torch.cuda.current_stream().wait_stream(s)
        graph = torch.cuda.CUDAGraph()
        if self._pool is None:
            self._pool = torch.cuda.graph_pool_handle()
        with torch.cuda.graph(graph, pool=self._pool):
            static_out = self._run(static_in, kc, vc, do_sample, top_k, temperature)
        g = SimpleNamespace(graph=graph, inp=static_in, out=static_out, kc=kc, vc=vc)
        self._graphs[key] = g
        return g

    # ------------------------------------------------------------- public API
    @torch.inference_mode()
    def generate(self, inputs_embeds=None, max_new_tokens=None, do_sample=True, top_p=1.0,
                 top_k=50, temperature=1.0, **_ignored):
        if max_new_tokens is not None and max_new_tokens != self.n_steps:
            raise ValueError(f"expected max_new_tokens={self.n_steps}, got {max_new_tokens}")
        if do_sample and top_p is not None and top_p < 1.0:
            raise NotImplementedError("top_p < 1 is not implemented in the fast predictor")
        if inputs_embeds.shape[1] != 2:
            raise ValueError("expected inputs_embeds of shape (B, 2, H)")
        self.calls += 1
        B, _, H = inputs_embeds.shape
        temperature = 1.0 if temperature is None else temperature
        if self.use_cuda_graph:
            g = self._graph_for(B, H, do_sample, top_k, temperature)
            g.inp.copy_(inputs_embeds)
            g.graph.replay()
            seq = g.out.clone()
        else:
            kc, vc = self._new_cache(B)
            seq = self._run(inputs_embeds, kc, vc, do_sample, top_k, temperature)
        return SimpleNamespace(sequences=seq)

    def release_graphs(self):
        self._graphs.clear()
        self._pool = None


def install(qwen_tts_model, **kw) -> FastCodePredictor:
    """Patch a loaded ``Qwen3TTSModel`` (or the inner model) in place. Returns the predictor."""
    inner = getattr(qwen_tts_model, "model", qwen_tts_model)
    cp = inner.talker.code_predictor
    uninstall(qwen_tts_model)
    fast = FastCodePredictor(cp, **kw)
    cp.generate = fast.generate          # instance attribute shadows GenerationMixin.generate
    cp._fast = fast
    return fast


def uninstall(qwen_tts_model) -> None:
    """Back to the library's own generate (drops any instance-level wrapper)."""
    inner = getattr(qwen_tts_model, "model", qwen_tts_model)
    cp = inner.talker.code_predictor
    fast = cp.__dict__.get("_fast")
    if fast is not None:
        fast.release_graphs()
    cp.__dict__.pop("_fast", None)
    cp.__dict__.pop("generate", None)
