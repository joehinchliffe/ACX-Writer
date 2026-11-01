"""Chapter assembly, mastering to a platform spec, parallel MP3 encoding and
a post-encode check of loudness / peak / noise floor."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np


@dataclass(frozen=True)
class Platform:
    name: str
    rms_db: Tuple[float, float]       # allowed RMS window (dBFS)
    target_rms_db: float
    peak_db: float                    # max sample peak after encode (dBFS)
    ceiling_db: float                 # limiter ceiling before encode (leaves mp3 headroom)
    noise_floor_db: float             # max RMS of the quietest 0.5 s window
    sample_rate: int
    bitrate: str
    head_s: float
    tail_s: float


PLATFORMS: Dict[str, Platform] = {
    # ACX / Audible: RMS -23..-18 dB, peak <= -3 dB, noise floor <= -60 dB, 44.1 kHz, 192 kbps CBR,
    # 0.5-1 s room tone at the head and 1-5 s at the tail.
    "acx": Platform("acx", (-23.0, -18.0), -20.0, -3.0, -3.6, -60.0, 44100, "192k", 0.75, 3.0),
    # Generic podcast / Spotify-style delivery.
    "generic": Platform("generic", (-21.0, -15.0), -18.0, -1.0, -1.5, -60.0, 44100, "128k", 0.5, 1.5),
}


def ffmpeg_bin() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as e:
        raise RuntimeError("ffmpeg not found (apt-get install ffmpeg, or pip install imageio-ffmpeg)") from e


def assemble(pieces: Sequence[Tuple[np.ndarray, float]], sr: int, head_s: float, tail_s: float,
             fade_ms: float = 4.0) -> np.ndarray:
    """pieces: (wav, pause_after_seconds). Short fades at every joint to avoid clicks."""
    f = max(1, int(fade_ms / 1000 * sr))
    ramp = np.linspace(0.0, 1.0, f, dtype=np.float32)
    out = [np.zeros(int(head_s * sr), np.float32)]
    for wav, pause in pieces:
        w = np.asarray(wav, np.float32).copy()
        if len(w) > 2 * f:
            w[:f] *= ramp
            w[-f:] *= ramp[::-1]
        out.append(w)
        if pause > 0:
            out.append(np.zeros(int(pause * sr), np.float32))
    out.append(np.zeros(int(tail_s * sr), np.float32))
    return np.concatenate(out)


def _db(x: float) -> float:
    return 20 * np.log10(max(x, 1e-12))


def rms_db(x: np.ndarray) -> float:
    return _db(float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))) if len(x) else -120.0


def limit(x: np.ndarray, sr: int, ceiling: float, hold_ms: float = 40.0) -> np.ndarray:
    """Look-ahead peak limiter, vectorised.

    Gain reduction r = peak/ceiling (>= 1) is spread with a centred max filter of
    +-hold, then smoothed with a moving average of width hold. Every sample's
    smoothing window lies inside the max-filter window of its own peak, so the
    ceiling is guaranteed while gain changes stay smooth (no clicks)."""
    from scipy.ndimage import maximum_filter1d, uniform_filter1d
    a = np.abs(x).astype(np.float32)
    if a.size == 0 or a.max() <= ceiling:
        return x
    L = max(1, int(hold_ms / 1000 * sr))
    r = np.maximum(1.0, a / ceiling)
    r = maximum_filter1d(r, size=2 * L + 1, mode="nearest")
    r = uniform_filter1d(r, size=L, mode="nearest")
    return (x / r).astype(np.float32)


def master(x: np.ndarray, sr: int, p: Platform) -> np.ndarray:
    gain = 10 ** ((p.target_rms_db - rms_db(x)) / 20)
    y = x * gain
    return limit(y, sr, 10 ** (p.ceiling_db / 20))


def encode_mp3(x: np.ndarray, sr: int, path: Path, p: Platform, tags: Dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = []
    for k, v in tags.items():
        if v:
            meta += ["-metadata", f"{k}={v}"]
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-f", "f32le", "-ar", str(sr),
           "-ac", "1", "-i", "pipe:0", "-af", "aresample=resampler=soxr:precision=28"
           if _has_soxr() else "aresample", "-ar", str(p.sample_rate), "-ac", "1",
           "-c:a", "libmp3lame", "-b:a", p.bitrate, "-write_xing", "1", *meta, str(path)]
    subprocess.run(cmd, input=np.ascontiguousarray(x, np.float32).tobytes(), check=True)
    return path


_SOXR = None


def _has_soxr() -> bool:
    global _SOXR
    if _SOXR is None:
        try:
            r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-h", "filter=aresample"],
                               capture_output=True, text=True)
            _SOXR = "soxr" in r.stdout
            if _SOXR:
                t = subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                                    "-i", "anullsrc=r=24000:cl=mono", "-t", "0.1", "-af",
                                    "aresample=resampler=soxr", "-ar", "44100", "-f", "null", "-"],
                                   capture_output=True)
                _SOXR = t.returncode == 0
        except Exception:
            _SOXR = False
    return _SOXR


def decode_any(path: Path, sr: int) -> np.ndarray:
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-i", str(path), "-f", "f32le",
           "-ac", "1", "-ar", str(sr), "pipe:1"]
    r = subprocess.run(cmd, capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.float32)


def measure(path: Path, p: Platform) -> Dict:
    x = decode_any(path, p.sample_rate)
    sr = p.sample_rate
    win = int(0.5 * sr)
    floors = []
    if len(x) > win:
        frames = x[: len(x) // win * win].reshape(-1, win)
        floors = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    floor_db = _db(float(np.min(floors))) if len(floors) else -120.0
    res = {"file": path.name, "duration_s": round(len(x) / sr, 2), "rms_db": round(rms_db(x), 2),
           "peak_db": round(_db(float(np.max(np.abs(x)))), 2), "noise_floor_db": round(floor_db, 1)}
    problems = []
    if not (p.rms_db[0] <= res["rms_db"] <= p.rms_db[1]):
        problems.append(f"RMS {res['rms_db']} dB outside {p.rms_db}")
    if res["peak_db"] > p.peak_db:
        problems.append(f"peak {res['peak_db']} dB above {p.peak_db}")
    if res["noise_floor_db"] > p.noise_floor_db:
        problems.append(f"noise floor {res['noise_floor_db']} dB above {p.noise_floor_db}")
    res["ok"] = not problems
    res["problems"] = problems
    return res


def master_and_encode_all(jobs: List[Tuple[np.ndarray, int, Path, Dict[str, str]]], p: Platform,
                          workers: Optional[int] = None) -> List[Dict]:
    """jobs: (raw_wav, sr, out_path, tags). Runs mastering + ffmpeg in parallel."""
    def one(job):
        x, sr, path, tags = job
        y = master(x, sr, p)
        encode_mp3(y, sr, path, p, tags)
        return measure(path, p)
    workers = workers or min(len(jobs), max(1, (os.cpu_count() or 4) - 1)) or 1
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(one, jobs))


def write_wav(path: Path, x: np.ndarray, sr: int) -> None:
    import soundfile as sf
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), x, sr, subtype="PCM_16")


def dump_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
