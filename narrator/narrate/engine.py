"""Batched Qwen3-TTS voice-clone synthesis with the Phase-2 speed changes as
independent switches, so each one can be measured on its own:

    fast_predictor  hand-written, CUDA-graphed code predictor (fast_predictor.py)
    sort            sort chunks by length before batching
    trim_ref        decode only the last `ref_context` reference frames in front
                    of each chunk (instead of the whole ~240-frame reference)
    batch_size      starting batch size, stepped down automatically on OOM

With every switch off, `synthesize` calls the library's own
`generate_voice_clone`, which is the Phase-1 baseline.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import torch

from . import fast_predictor


@dataclass
class EngineConfig:
    model: str = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
    device: str = "cuda:0"
    dtype: str = "bfloat16"
    fast_predictor: bool = True
    sort: bool = True
    trim_ref: bool = True
    ref_context: int = 25            # frames of reference kept as decoder left context
    decode_group: int = 8            # sequences per tokenizer-decode call
    batch_size: int = 96
    min_batch_size: int = 4
    chars_per_sec: float = 14.5      # for the per-batch frame cap
    frame_cap_factor: float = 2.2    # cap = factor * expected frames + 25
    gen_kwargs: Dict = field(default_factory=dict)   # e.g. temperature/top_k overrides


@dataclass
class Take:
    wav: np.ndarray
    frames: int
    hit_cap: bool


@dataclass
class SynthStats:
    audio_s: float = 0.0
    gen_s: float = 0.0               # talker + code predictor
    predictor_s: float = 0.0         # code predictor only (when profiling)
    decode_s: float = 0.0
    wall_s: float = 0.0
    peak_vram_gb: float = 0.0
    batches: int = 0
    oom_stepdowns: int = 0
    batch_size_final: int = 0

    @property
    def xrt(self) -> float:
        return self.audio_s / self.wall_s if self.wall_s else 0.0

    def as_dict(self):
        d = dict(self.__dict__)
        d["xrt"] = round(self.xrt, 2)
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in d.items()}


class Engine:
    def __init__(self, cfg: EngineConfig, profile_predictor: bool = False):
        from qwen_tts import Qwen3TTSModel
        self.cfg = cfg
        dtype = getattr(torch, cfg.dtype)
        self.tts = Qwen3TTSModel.from_pretrained(cfg.model, device_map=cfg.device, dtype=dtype,
                                                 attn_implementation="sdpa")
        self.inner = self.tts.model
        st = self.inner.speech_tokenizer
        self.sr = int(st.get_output_sample_rate())
        self.upsample = int(st.get_decode_upsample_rate())
        self.languages = {l.lower() for l in (self.tts.get_supported_languages() or [])}
        self.fast = fast_predictor.install(self.tts) if cfg.fast_predictor else None
        self.batch_size = cfg.batch_size
        self._predictor_time = 0.0
        self.profiling = False           # syncs around every predictor call: profiling runs only
        if profile_predictor:
            self._wrap_predictor_timer()

    # ----------------------------------------------------------- utilities
    def configure(self, **kw):
        """Change switches without reloading the model (used by the benchmark ladder)."""
        for k, v in kw.items():
            if not hasattr(self.cfg, k):
                raise AttributeError(k)
            setattr(self.cfg, k, v)
        if "batch_size" in kw:
            self.batch_size = kw["batch_size"]
        if "fast_predictor" in kw:
            timer = getattr(self, "_timer_on", False)
            fast_predictor.uninstall(self.tts)
            self.fast = fast_predictor.install(self.tts) if kw["fast_predictor"] else None
            if timer:
                self._wrap_predictor_timer()

    def _wrap_predictor_timer(self):
        self._timer_on = True
        cp = self.inner.talker.code_predictor
        inner_gen = cp.generate

        def timed(*a, **kw):
            if not self.profiling:
                return inner_gen(*a, **kw)
            torch.cuda.synchronize()
            t = time.perf_counter()
            r = inner_gen(*a, **kw)
            torch.cuda.synchronize()
            self._predictor_time += time.perf_counter() - t
            return r
        cp.generate = timed

    def check_language(self, language: str):
        if self.languages and language.lower() not in self.languages:
            raise ValueError(f"language {language!r} is not supported by this model "
                             f"(supported: {sorted(self.languages)})")

    def voice(self, ref_wav: str, ref_text: str):
        """Build the clone prompt once per voice (ICL mode: needs the EXACT transcript)."""
        items = self.tts.create_voice_clone_prompt(ref_audio=ref_wav, ref_text=ref_text,
                                                   x_vector_only_mode=False)
        v = items[0]
        v._ref_ids = self.tts._tokenize_texts([self.tts._build_ref_text(ref_text)])[0]
        return v

    def _cap(self, texts: Sequence[str]) -> int:
        longest = max(len(t) for t in texts)
        exp_frames = 12.5 * longest / self.cfg.chars_per_sec
        return int(min(2048, max(60, self.cfg.frame_cap_factor * exp_frames + 25)))

    # -------------------------------------------------------------- core
    def _codes(self, texts: List[str], voice, language: str, cap: int) -> List[torch.Tensor]:
        n = len(texts)
        input_ids = self.tts._tokenize_texts([self.tts._build_assistant_text(t) for t in texts])
        prompt = self.tts._prompt_items_to_voice_clone_prompt([voice] * n)
        kw = self.tts._merge_generate_kwargs(max_new_tokens=cap, **self.cfg.gen_kwargs)
        with torch.inference_mode():
            codes, _ = self.inner.generate(input_ids=input_ids, ref_ids=[voice._ref_ids] * n,
                                           voice_clone_prompt=prompt, languages=[language] * n,
                                           non_streaming_mode=False, **kw)
        return codes

    def _decode(self, codes: List[torch.Tensor], voice) -> List[np.ndarray]:
        st = self.inner.speech_tokenizer
        ref = voice.ref_code
        out: List[np.ndarray] = []
        g = self.cfg.decode_group if self.cfg.trim_ref else len(codes)
        for i in range(0, len(codes), g):
            grp = codes[i:i + g]
            if self.cfg.trim_ref:
                ctx = ref[-self.cfg.ref_context:].to(grp[0].device)
                full = [torch.cat([ctx, c], dim=0) for c in grp]
            else:
                full = [torch.cat([ref.to(c.device), c], dim=0) for c in grp]
            with torch.inference_mode():
                wavs, _ = st.decode([{"audio_codes": c} for c in full])
            for w, c, f in zip(wavs, grp, full):
                if self.cfg.trim_ref:
                    # decoder output is exactly frames * upsample samples, so the
                    # context is cut sample-exactly (= the library's proportional cut)
                    cut = self.cfg.ref_context if ref.shape[0] >= self.cfg.ref_context else ref.shape[0]
                    out.append(w[cut * self.upsample:])
                else:
                    cut = int(ref.shape[0] / max(f.shape[0], 1) * w.shape[0])   # library's cut
                    out.append(w[cut:])
        return out

    def _stock(self, texts: List[str], voice, language: str, cap: int) -> List[Take]:
        kw = dict(self.cfg.gen_kwargs, max_new_tokens=cap)
        wavs, _ = self.tts.generate_voice_clone(text=texts, language=[language] * len(texts),
                                                voice_clone_prompt=[voice] * len(texts), **kw)
        frames = [int(round(len(w) / self.upsample)) for w in wavs]
        return [Take(w, f, f >= cap) for w, f in zip(wavs, frames)]

    def synthesize(self, texts: Sequence[str], voice, language: str,
                   progress: Optional[Callable[[int, int], None]] = None) -> Tuple[List[Take], SynthStats]:
        stats = SynthStats()
        self.check_language(language)
        texts = list(texts)
        order = sorted(range(len(texts)), key=lambda i: -len(texts[i])) if self.cfg.sort \
            else list(range(len(texts)))
        results: List[Optional[Take]] = [None] * len(texts)
        stock = not (self.cfg.fast_predictor or self.cfg.sort or self.cfg.trim_ref)
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        t_start = time.perf_counter()
        pos = 0
        while pos < len(order):
            bs = min(self.batch_size, len(order) - pos)
            idx = order[pos:pos + bs]
            batch = [texts[i] for i in idx]
            cap = self._cap(batch)
            try:
                self._predictor_time = 0.0
                _sync()
                t0 = time.perf_counter()
                if stock:
                    takes = self._stock(batch, voice, language, cap)
                    _sync()
                    stats.gen_s += time.perf_counter() - t0
                else:
                    codes = self._codes(batch, voice, language, cap)
                    _sync()
                    t1 = time.perf_counter()
                    stats.gen_s += t1 - t0
                    wavs = self._decode(codes, voice)
                    _sync()
                    stats.decode_s += time.perf_counter() - t1
                    takes = [Take(w, int(c.shape[0]), int(c.shape[0]) >= cap) for w, c in zip(wavs, codes)]
                stats.predictor_s += self._predictor_time
            except torch.cuda.OutOfMemoryError:
                torch.cuda.empty_cache()
                if bs <= self.cfg.min_batch_size:
                    raise
                self.batch_size = max(self.cfg.min_batch_size, bs // 2)
                stats.oom_stepdowns += 1
                if self.fast is not None:
                    self.fast.release_graphs()
                print(f"[engine] CUDA OOM at batch {bs}; stepping down to {self.batch_size}", flush=True)
                continue
            for i, tk in zip(idx, takes):
                results[i] = tk
            pos += bs
            stats.batches += 1
            if progress:
                progress(pos, len(order))
        _sync()
        stats.wall_s = time.perf_counter() - t_start
        stats.audio_s = sum(len(t.wav) for t in results) / self.sr
        stats.batch_size_final = self.batch_size
        if torch.cuda.is_available():
            stats.peak_vram_gb = torch.cuda.max_memory_allocated() / 2 ** 30
        return results, stats

    def free_cache(self):
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def _sync():
    if torch.cuda.is_available():
        torch.cuda.synchronize()
