"""CPU checks of the fast code predictor against the stock qwen-tts / HF path.

Uses the real library classes with small random weights (the real checkpoint
is checked on the GPU by bench/exactness.py).
"""
import copy

import pytest
import torch

pytest.importorskip("qwen_tts")
from qwen_tts.core.models.configuration_qwen3_tts import Qwen3TTSTalkerConfig  # noqa: E402
from qwen_tts.core.models.modeling_qwen3_tts import Qwen3TTSTalkerForConditionalGeneration  # noqa: E402

from narrate.fast_predictor import FastCodePredictor  # noqa: E402


def tiny_talker(dtype=torch.float32, seed=0):
    torch.manual_seed(seed)
    cp_cfg = dict(vocab_size=128, hidden_size=64, intermediate_size=128, num_hidden_layers=5,
                  num_attention_heads=4, num_key_value_heads=2, head_dim=32, num_code_groups=16,
                  initializer_range=0.2)
    cfg = Qwen3TTSTalkerConfig(
        code_predictor_config=cp_cfg, vocab_size=160, hidden_size=96, intermediate_size=192,
        num_hidden_layers=2, num_attention_heads=4, num_key_value_heads=2, head_dim=24,
        num_code_groups=16, text_hidden_size=48, initializer_range=0.2,
        rope_scaling={"rope_type": "default", "mrope_section": [4, 4, 4], "interleaved": True},
        codec_eos_token_id=150, codec_pad_id=151, codec_bos_id=152, text_vocab_size=64,
    )
    cfg._attn_implementation = "sdpa"
    cfg.code_predictor_config._attn_implementation = "sdpa"
    model = Qwen3TTSTalkerForConditionalGeneration(cfg).eval().to(dtype)
    return model


def stock_codes(cp, embeds, do_sample=False, **kw):
    with torch.inference_mode():
        r = cp.generate(inputs_embeds=embeds, max_new_tokens=cp.config.num_code_groups - 1,
                        do_sample=do_sample, top_p=1.0, top_k=kw.get("top_k", 50),
                        temperature=kw.get("temperature", 0.9),
                        output_hidden_states=True, return_dict_in_generate=True)
    return r.sequences


@pytest.mark.parametrize("dtype", [torch.float32, torch.bfloat16])
@pytest.mark.parametrize("B", [1, 7, 24])
def test_greedy_bit_identical(dtype, B):
    talker = tiny_talker(dtype)
    cp = talker.code_predictor
    fast = FastCodePredictor(cp, use_cuda_graph=False)
    torch.manual_seed(1)
    frames = 0
    for _ in range(-(-300 // B)):              # a few hundred frames
        emb = (torch.randn(B, 2, talker.config.hidden_size) * 2).to(dtype)
        ref = stock_codes(cp, emb)
        got = fast.generate(inputs_embeds=emb, max_new_tokens=15, do_sample=False).sequences
        assert ref.shape == (B, 15)
        assert torch.equal(ref, got)
        frames += B
    assert frames >= 300


def test_sampling_distribution_matches():
    """First and last residual token distributions: stock multinomial vs exponential race."""
    talker = tiny_talker(torch.float32, seed=3)
    cp = talker.code_predictor
    fast = FastCodePredictor(cp, use_cuda_graph=False)
    torch.manual_seed(5)
    emb = torch.randn(1, 2, talker.config.hidden_size)
    N = 16000
    embN = emb.expand(N, 2, -1).contiguous()
    torch.manual_seed(11)
    a = stock_codes(cp, embN, do_sample=True, top_k=50, temperature=0.9)
    torch.manual_seed(13)
    a2 = stock_codes(cp, embN, do_sample=True, top_k=50, temperature=0.9)   # null: stock vs stock
    torch.manual_seed(12)
    b = fast.generate(inputs_embeds=embN, max_new_tokens=15, do_sample=True, top_k=50,
                      temperature=0.9).sequences
    V = cp.config.vocab_size

    def tv(x, y, col):
        px = torch.bincount(x[:, col], minlength=V).float() / N
        py = torch.bincount(y[:, col], minlength=V).float() / N
        return 0.5 * (px - py).abs().sum().item()

    for col in (0, 1, 7, 14):
        null, got = tv(a, a2, col), tv(a, b, col)
        assert got < 1.5 * null + 0.01, f"codebook {col + 1}: TV {got:.3f} vs stock-vs-stock {null:.3f}"
    # top-k respected: never more than 50 distinct values for the first token (fixed input)
    assert torch.unique(b[:, 0]).numel() <= 50


def test_full_talker_generate_greedy_identical():
    """Run the library's own talker.generate loop with the predictor patched in."""
    talker = tiny_talker(torch.float32, seed=7)
    torch.manual_seed(2)
    B, T = 3, 6
    H = talker.config.hidden_size
    embeds = torch.randn(B, T, H)
    mask = torch.ones(B, T, dtype=torch.long)
    mask[0, :2] = 0                            # left padding like the library
    trailing = torch.randn(B, 5, H)
    pad = torch.randn(1, 1, H)
    kw = dict(inputs_embeds=embeds, attention_mask=mask, trailing_text_hidden=trailing,
              tts_pad_embed=pad, max_new_tokens=40, min_new_tokens=2, do_sample=False,
              subtalker_dosample=False, subtalker_top_k=50, subtalker_top_p=1.0,
              subtalker_temperature=0.9, eos_token_id=talker.config.codec_eos_token_id,
              repetition_penalty=1.05, output_hidden_states=True, return_dict_in_generate=True)

    def codes(model):
        model.rope_deltas = None
        with torch.inference_mode():
            r = model.generate(**kw)
        return torch.stack([h[-1] for h in r.hidden_states if h[-1] is not None], dim=1)

    ref = codes(talker)
    patched = copy.deepcopy(talker)
    fast = FastCodePredictor(patched.code_predictor, use_cuda_graph=False)
    patched.code_predictor.generate = fast.generate
    got = codes(patched)
    assert fast.calls > 0
    assert torch.equal(ref, got)
