"""Quality gate: transcribe every chunk with faster-whisper and compare it with
the ORIGINAL text (not the respelled TTS text), plus cheap audio checks."""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np

from .text import numbers_to_words

WHISPER_SR = 16000


# ------------------------------------------------------------ text compare
def normalize_words(text: str, lang: str = "en") -> List[str]:
    t = unicodedata.normalize("NFKC", text)
    t = re.sub(r"[‘’`´]", "'", t)
    t = numbers_to_words(t, lang)
    t = t.lower()
    t = re.sub(r"[-–—/]", " ", t)
    t = re.sub(r"'(?!\w)|(?<!\w)'", " ", t)                  # quotes, keep don't / it's
    t = re.sub(r"[^\w\s']", " ", t)
    return " ".join(_CANON.get(w, w) for w in t.split()).split()


_CANON = {"ok": "okay", "mr": "mister", "mrs": "missus", "dr": "doctor", "vs": "versus",
          "towards": "toward", "alright": "all right"}


def merge_compounds(ref: List[str], hyp: List[str]):
    """'river bank' vs 'riverbank': join adjacent words when the joined form is
    what the other side has, so spacing differences are not counted as errors."""
    def merge(a, other):
        vocab, out, i = set(other), [], 0
        while i < len(a):
            if i + 1 < len(a) and a[i] + a[i + 1] in vocab:
                out.append(a[i] + a[i + 1])
                i += 2
            else:
                out.append(a[i])
                i += 1
        return out
    return merge(ref, hyp), merge(hyp, ref)


def edit_ops(ref: Sequence[str], hyp: Sequence[str]):
    """Levenshtein on words. Returns (distance, subs, dels, ins)."""
    n, m = len(ref), len(hyp)
    d = np.zeros((n + 1, m + 1), dtype=np.int32)
    d[:, 0] = np.arange(n + 1)
    d[0, :] = np.arange(m + 1)
    for i in range(1, n + 1):
        ri = ref[i - 1]
        for j in range(1, m + 1):
            c = 0 if ri == hyp[j - 1] else 1
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1, d[i - 1, j - 1] + c)
    i, j, s, de, ins = n, m, 0, 0, 0
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i, j] == d[i - 1, j - 1] + (ref[i - 1] != hyp[j - 1]):
            s += ref[i - 1] != hyp[j - 1]
            i, j = i - 1, j - 1
        elif i > 0 and d[i, j] == d[i - 1, j] + 1:
            de += 1
            i -= 1
        else:
            ins += 1
            j -= 1
    return int(d[n, m]), s, de, ins


# ------------------------------------------------------------- audio checks
def audio_checks(wav: np.ndarray, sr: int) -> Dict[str, float]:
    x = wav.astype(np.float32)
    out = {"duration": len(x) / sr, "peak": float(np.max(np.abs(x))) if len(x) else 0.0}
    out["clipped_frac"] = float(np.mean(np.abs(x) >= 0.999)) if len(x) else 0.0
    # click at the start: a spike in the first 15 ms much louder than the next 200 ms
    a, b = int(0.015 * sr), int(0.215 * sr)
    if len(x) > b:
        head = float(np.max(np.abs(x[:a])))
        body_rms = float(np.sqrt(np.mean(x[a:b] ** 2)) + 1e-6)
        out["start_click"] = head / body_rms if head > 0.08 else 0.0
    else:
        out["start_click"] = 0.0
    # longest internal silence (50 ms frames below -50 dBFS), ignoring head/tail
    hop = int(0.05 * sr)
    if len(x) > 4 * hop:
        frames = x[: len(x) // hop * hop].reshape(-1, hop)
        rms = np.sqrt(np.mean(frames ** 2, axis=1))
        voiced = np.where(rms > 10 ** (-50 / 20))[0]
        longest = 0
        if len(voiced) > 1:
            gaps = np.diff(voiced) - 1
            longest = int(gaps.max()) if len(gaps) else 0
        out["max_gap"] = longest * hop / sr
        out["lead_silence"] = (voiced[0] * hop / sr) if len(voiced) else out["duration"]
    else:
        out["max_gap"] = 0.0
        out["lead_silence"] = 0.0
    return out


@dataclass
class Verdict:
    ok: bool
    score: float                 # lower is better (used to pick the best take)
    wer: float
    errors: int
    n_words: int
    hyp: str
    reasons: List[str] = field(default_factory=list)
    audio: Dict[str, float] = field(default_factory=dict)


@dataclass
class QCConfig:
    max_wer: float = 0.08            # allowed word error rate ...
    min_errors_allowed: int = 1      # ... but always allow this many errors (short chunks)
    max_gap: float = 2.0             # seconds of silence inside a chunk
    chars_per_sec: float = 14.5      # typical narration speed (English)
    max_speed_ratio: float = 2.2     # duration > ratio * expected -> rambling / stretched
    min_speed_ratio: float = 0.40
    max_start_click: float = 8.0


def judge(original: str, hyp: str, wav: np.ndarray, sr: int, lang: str, cfg: QCConfig,
          hit_frame_cap: bool = False) -> Verdict:
    ref_w = normalize_words(original, lang)
    hyp_w = normalize_words(hyp, lang)
    ref_w, hyp_w = merge_compounds(ref_w, hyp_w)
    dist, s, de, ins = edit_ops(ref_w, hyp_w)
    n = max(1, len(ref_w))
    wer = dist / n
    au = audio_checks(wav, sr)
    reasons = []
    allowed = max(cfg.min_errors_allowed, math.floor(cfg.max_wer * n))
    if dist > allowed:
        reasons.append(f"wer {wer:.2f} ({s} sub, {de} del, {ins} ins)")
    exp = max(0.6, len(original) / cfg.chars_per_sec)
    ratio = au["duration"] / exp
    if ratio > cfg.max_speed_ratio:
        reasons.append(f"too long {au['duration']:.1f}s vs ~{exp:.1f}s")
    if ratio < cfg.min_speed_ratio:
        reasons.append(f"too short {au['duration']:.1f}s vs ~{exp:.1f}s")
    if au["max_gap"] > cfg.max_gap:
        reasons.append(f"internal silence {au['max_gap']:.1f}s")
    if au["start_click"] > cfg.max_start_click:
        reasons.append(f"click at start ({au['start_click']:.0f}x)")
    if au["clipped_frac"] > 1e-3:
        reasons.append("clipping")
    if hit_frame_cap:
        reasons.append("hit frame cap (no end-of-speech)")
    score = wer + 0.5 * len([r for r in reasons if not r.startswith("wer")]) + max(0.0, ratio - 1.6)
    return Verdict(not reasons, score, wer, dist, len(ref_w), hyp, reasons, au)


# ---------------------------------------------------------------- whisper
class WhisperChecker:
    def __init__(self, model: str = "large-v3-turbo", device: str = "cuda",
                 compute_type: str = "float16", batch_size: int = 16):
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()     # CTranslate2 OOMs next to PyTorch's cache otherwise
        except ImportError:
            pass
        from faster_whisper import BatchedInferencePipeline, WhisperModel
        self.model = WhisperModel(model, device=device, compute_type=compute_type)
        self.pipe = BatchedInferencePipeline(self.model)
        self.batch_size = batch_size

    def transcribe_many(self, wavs: List[np.ndarray], sr: int, lang: str,
                        hotwords: Optional[str] = None) -> List[str]:
        """Concatenate chunks with gaps and hand Whisper one clip per chunk, so
        many short chunks share one batch."""
        import librosa
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        out = [""] * len(wavs)
        gap = np.zeros(int(0.5 * WHISPER_SR), np.float32)
        pieces, clips, idx, t = [], [], [], 0.0
        long_ones = []
        for i, w in enumerate(wavs):
            a = librosa.resample(w.astype(np.float32), orig_sr=sr, target_sr=WHISPER_SR) if sr != WHISPER_SR else w
            dur = len(a) / WHISPER_SR
            if dur >= 29.5:
                long_ones.append((i, a))
                continue
            clips.append({"start": t, "end": t + dur})
            idx.append(i)
            pieces += [a, gap]
            t += dur + len(gap) / WHISPER_SR
        if clips:
            audio = np.concatenate(pieces).astype(np.float32)
            segs, _ = self.pipe.transcribe(audio, language=lang, batch_size=self.batch_size,
                                           clip_timestamps=clips, vad_filter=False,
                                           without_timestamps=True, beam_size=1,
                                           hotwords=hotwords)
            starts = np.array([c["start"] for c in clips])
            for seg in segs:
                k = int(np.searchsorted(starts, seg.start + 1e-3, side="right") - 1)
                k = min(max(k, 0), len(idx) - 1)
                out[idx[k]] = (out[idx[k]] + " " + seg.text).strip()
        for i, a in long_ones:
            segs, _ = self.model.transcribe(a, language=lang, beam_size=1, vad_filter=False,
                                            condition_on_previous_text=False, hotwords=hotwords)
            out[i] = " ".join(s.text.strip() for s in segs)
        return out


WHISPER_LANG = {"english": "en", "chinese": "zh", "japanese": "ja", "korean": "ko", "german": "de",
                "french": "fr", "russian": "ru", "portuguese": "pt", "spanish": "es",
                "italian": "it"}
