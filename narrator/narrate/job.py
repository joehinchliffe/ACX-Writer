"""Narrate one or more books on the current GPU machine.

    python -m narrate.job books/my-book [books/other ...] --out out/

Each book folder holds book.json, a voice reference, and either chapters/*.md
or manuscript.md (see README). Several books share one model load (and one
pod boot). Output per book: out/<book>/<NN Title>.mp3, report.json, and a
review/ folder with any chunk that still failed the check after all redos.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from . import audio
from .engine import Engine, EngineConfig, Take
from .qc import WHISPER_LANG, QCConfig, Verdict, WhisperChecker, judge, normalize_words, edit_ops
from .text import NOT_SUPPORTED_HINT, QWEN_LANGUAGES, Chunk, chunk_chapter, load_book_text, tts_text


def log(msg: str) -> None:
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "", s).strip()[:80] or "untitled"


# ------------------------------------------------------------------ config
def load_book(book_dir: Path) -> Dict:
    cfg = json.loads((book_dir / "book.json").read_text(encoding="utf-8"))
    cfg.setdefault("language", "english")
    cfg.setdefault("respell", {})
    cfg.setdefault("platform", "acx")
    cfg.setdefault("expand_numbers", True)
    cfg.setdefault("max_chars", 300)
    lang = cfg["language"].lower()
    if lang in NOT_SUPPORTED_HINT:
        raise SystemExit(f"{book_dir.name}: {NOT_SUPPORTED_HINT[lang]}")
    if lang not in QWEN_LANGUAGES:
        raise SystemExit(f"{book_dir.name}: language {lang!r} is not one Qwen3-TTS supports "
                         f"({', '.join(QWEN_LANGUAGES)})")
    v = cfg["voice"]
    wav = (book_dir / v["wav"]).resolve()
    text = v.get("text") or (book_dir / v["text_file"]).read_text(encoding="utf-8").strip()
    cfg["_voice"] = (str(wav), text)
    respell_file = book_dir / "respell.json"
    if respell_file.exists():
        cfg["respell"].update(json.loads(respell_file.read_text(encoding="utf-8")))
    return cfg


# ----------------------------------------------------------- reference QC
def check_reference(whisper: WhisperChecker, wav_path: str, text: str, lang_code: str,
                    max_wer: float = 0.02) -> Dict:
    import soundfile as sf
    x, sr = sf.read(wav_path, dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    dur = len(x) / sr
    hyp = whisper.transcribe_many([x], sr, lang_code)[0] if dur < 29.5 else \
        " ".join(whisper.transcribe_many([x[i:i + int(25 * sr)] for i in range(0, len(x), int(25 * sr))],
                                         sr, lang_code))
    ref_w, hyp_w = normalize_words(text, lang_code), normalize_words(hyp, lang_code)
    dist = edit_ops(ref_w, hyp_w)[0]
    wer = dist / max(1, len(ref_w))
    warnings = []
    if not 18 <= dur <= 27:
        warnings.append(f"reference is {dur:.1f}s; 20-25 s of clean speech works best")
    peak = float(np.max(np.abs(x)))
    if peak >= 0.99:
        warnings.append("reference clips")
    return {"duration_s": round(dur, 2), "wer": round(wer, 3), "whisper": hyp,
            "ok": wer <= max_wer, "warnings": warnings}


# --------------------------------------------------------------- chunk cache
def _hash(chunk: Chunk, voice_key: str) -> str:
    return hashlib.sha1(f"{voice_key}\n{chunk.tts_text}".encode()).hexdigest()[:16]


class ChunkCache:
    """Accepted takes saved as FLAC so a rerun (or a dead pod) never redoes them."""

    def __init__(self, root: Path, sr: int):
        self.root, self.sr = root, sr
        root.mkdir(parents=True, exist_ok=True)
        self.index_path = root / "index.json"
        self.index = json.loads(self.index_path.read_text()) if self.index_path.exists() else {}

    def get(self, cid: str, h: str) -> Optional[np.ndarray]:
        e = self.index.get(cid)
        if e and e["hash"] == h and (self.root / f"{cid}.flac").exists():
            import soundfile as sf
            return sf.read(str(self.root / f"{cid}.flac"), dtype="float32")[0]
        return None

    def put(self, cid: str, h: str, wav: np.ndarray, verdict: Dict):
        import soundfile as sf
        sf.write(str(self.root / f"{cid}.flac"), wav, self.sr)
        self.index[cid] = {"hash": h, **verdict}

    def save(self):
        self.index_path.write_text(json.dumps(self.index, indent=1))


# ------------------------------------------------------------------- book
class Narrator:
    def __init__(self, ecfg: EngineConfig, qcfg: QCConfig, max_redos: int = 3,
                 whisper_model: str = "large-v3-turbo", skip_qc: bool = False,
                 force_reference: bool = False):
        self.ecfg, self.qcfg = ecfg, qcfg
        self.max_redos = max_redos
        self.skip_qc = skip_qc
        self.force_reference = force_reference
        log(f"loading {ecfg.model}")
        self.engine = Engine(ecfg)
        self.whisper: Optional[WhisperChecker] = None
        self.whisper_model = whisper_model
        self._voices: Dict = {}

    def _whisper(self) -> WhisperChecker:
        if self.whisper is None:
            self.engine.free_cache()
            log(f"loading faster-whisper {self.whisper_model}")
            self.whisper = WhisperChecker(self.whisper_model)
        return self.whisper

    def run_book(self, book_dir: Path, out_root: Path) -> Dict:
        t_book = time.perf_counter()
        cfg = load_book(book_dir)
        lang = cfg["language"].lower()
        lang_code = WHISPER_LANG[lang]
        title = cfg.get("title") or book_dir.name
        out_dir = out_root / safe_name(book_dir.name)
        out_dir.mkdir(parents=True, exist_ok=True)
        report: Dict = {"book": book_dir.name, "title": title, "language": lang}

        # reference check (Whisper must agree with the transcript you gave)
        wav_path, ref_text = cfg["_voice"]
        if not self.skip_qc:
            rc = check_reference(self._whisper(), wav_path, ref_text, lang_code)
            report["reference"] = rc
            for w in rc["warnings"]:
                log(f"reference: {w}")
            if not rc["ok"] and not self.force_reference:
                raise SystemExit(f"{book_dir.name}: the reference transcript does not match the WAV "
                                 f"(WER {rc['wer']:.2f}). Whisper heard: {rc['whisper']!r}. Fix voice text "
                                 f"or pass --force-reference.")
        key = (wav_path, ref_text)
        if key not in self._voices:
            self._voices[key] = self.engine.voice(wav_path, ref_text)
        voice = self._voices[key]
        voice_key = hashlib.sha1(f"{wav_path}|{ref_text}|{Path(wav_path).stat().st_size}".encode()).hexdigest()

        # text -> chunks
        chapters = load_book_text(book_dir)
        chunks: List[Chunk] = []
        for ch in chapters:
            chunks += chunk_chapter(ch, max_chars=int(cfg["max_chars"]))
        for c in chunks:
            c.tts_text = tts_text(c.text, QWEN_LANGUAGES[lang], cfg["respell"], cfg["expand_numbers"])
        n_long = sum(c.long_split for c in chunks)
        log(f"{title}: {len(chapters)} chapters, {len(chunks)} chunks"
            + (f" ({n_long} from over-long sentences split at clause punctuation)" if n_long else ""))

        cache = ChunkCache(out_root / ".work" / safe_name(book_dir.name), self.engine.sr)
        best: Dict[str, Take] = {}
        verdicts: Dict[str, Verdict] = {}
        hashes = {c.id: _hash(c, voice_key) for c in chunks}
        todo = []
        for c in chunks:
            w = cache.get(c.id, hashes[c.id])
            if w is not None:
                best[c.id] = Take(w, int(len(w) / self.engine.upsample), False)
            else:
                todo.append(c)
        if len(todo) < len(chunks):
            log(f"reusing {len(chunks) - len(todo)} cached chunks")

        gen_stats = []
        redo_counts: Dict[str, int] = {}
        attempt = 0
        hotwords = " ".join(sorted(cfg["respell"].keys()))[:200] or None
        while todo:
            label = "generate" if attempt == 0 else f"redo {attempt}"
            log(f"{label}: {len(todo)} chunks")
            takes, st = self.engine.synthesize([c.tts_text for c in todo], voice, lang,
                                               progress=_progress(label))
            gen_stats.append(st.as_dict())
            log(f"{label}: {st.audio_s / 60:.1f} min audio in {st.wall_s:.0f}s = {st.xrt:.1f}x real time "
                f"(batch {st.batch_size_final}, peak {st.peak_vram_gb:.1f} GB)")
            if self.skip_qc:
                for c, tk in zip(todo, takes):
                    best[c.id] = tk
                break
            hyps = self._whisper().transcribe_many([t.wav for t in takes], self.engine.sr, lang_code,
                                                   hotwords=hotwords)
            failed = []
            for c, tk, hyp in zip(todo, takes, hyps):
                v = judge(c.text, hyp, tk.wav, self.engine.sr, lang_code, self.qcfg, tk.hit_cap)
                if c.id not in verdicts or v.score < verdicts[c.id].score:
                    verdicts[c.id], best[c.id] = v, tk
                if v.ok:
                    cache.put(c.id, hashes[c.id], tk.wav, {"wer": v.wer, "redos": attempt})
                elif attempt < self.max_redos:
                    failed.append(c)
                    redo_counts[c.id] = attempt + 1
            cache.save()
            log(f"{label}: {len(todo) - len(failed)} passed, {len(failed)} to redo")
            todo = failed
            attempt += 1
            self.engine.free_cache()

        # still-failing chunks: keep best take, save for a human
        still_bad = [c for c in chunks if c.id in verdicts and not verdicts[c.id].ok]
        review = out_dir / "review"
        for c in still_bad:
            audio.write_wav(review / f"{c.id}.wav", best[c.id].wav, self.engine.sr)
        if still_bad:
            audio.dump_json(review / "failures.json", [
                {"id": c.id, "text": c.text, "tts_text": c.tts_text, "heard": verdicts[c.id].hyp,
                 "reasons": verdicts[c.id].reasons} for c in still_bad])
        t_gen_done = time.perf_counter()

        # assemble + master + encode (parallel)
        platform = audio.PLATFORMS[cfg["platform"]]
        jobs = []
        by_ch: Dict[int, List[Chunk]] = {}
        for c in chunks:
            by_ch.setdefault(c.chapter, []).append(c)
        for ch in chapters:
            parts = [(best[c.id].wav, c.pause_after) for c in by_ch.get(ch.index, [])]
            if not parts:
                continue
            raw = audio.assemble(parts, self.engine.sr, platform.head_s, platform.tail_s)
            name = f"{ch.index:02d} {safe_name(ch.title)}.mp3"
            tags = {"title": ch.title, "artist": cfg.get("author", ""), "album": title,
                    "album_artist": cfg.get("author", ""), "track": str(ch.index),
                    "genre": "Audiobook", "comment": f"Narrated by {cfg.get('narrator', '')}".strip()}
            jobs.append((raw, self.engine.sr, out_dir / name, tags))
        log(f"mastering + encoding {len(jobs)} chapters ({platform.name})")
        files = audio.master_and_encode_all(jobs, platform)
        t_end = time.perf_counter()

        audio_s = sum(f["duration_s"] for f in files)
        gen_audio = sum(s["audio_s"] for s in gen_stats)
        gen_wall = sum(s["wall_s"] for s in gen_stats[:1])
        report.update({
            "chapters": len(chapters), "chunks": len(chunks),
            "finished_audio_min": round(audio_s / 60, 2),
            "raw_xrt_first_pass": round(gen_stats[0]["audio_s"] / gen_wall, 2) if gen_stats and gen_wall else None,
            "end_to_end_xrt": round(audio_s / (t_end - t_book), 2),
            "wall_s": {"total": round(t_end - t_book, 1), "generate_and_qc": round(t_gen_done - t_book, 1),
                       "encode": round(t_end - t_gen_done, 1)},
            "generated_audio_min_incl_redos": round(gen_audio / 60, 2),
            "redos": {"chunks_redone": len(redo_counts),
                      "takes": sum(redo_counts.values()),
                      "still_failing": [c.id for c in still_bad]},
            "passes": gen_stats,
            "files": files,
            "engine": asdict(self.ecfg),
        })
        bad_files = [f for f in files if not f["ok"]]
        audio.dump_json(out_dir / "report.json", report)
        log(f"{title}: {audio_s / 60:.1f} min finished audio, end-to-end {report['end_to_end_xrt']}x real "
            f"time, {len(redo_counts)} chunks redone, {len(still_bad)} still failing"
            + (f", {len(bad_files)} files outside {platform.name} spec" if bad_files else ""))
        return report


def _progress(label):
    last = [0.0]

    def cb(done, total):
        now = time.perf_counter()
        if now - last[0] > 30 or done == total:
            last[0] = now
            log(f"  {label}: {done}/{total} chunks")
    return cb


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("books", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=Path("out"))
    ap.add_argument("--model", default=EngineConfig.model)
    ap.add_argument("--batch", type=int, default=EngineConfig.batch_size)
    ap.add_argument("--no-fast-predictor", action="store_true")
    ap.add_argument("--no-sort", action="store_true")
    ap.add_argument("--no-trim-ref", action="store_true")
    ap.add_argument("--max-redos", type=int, default=3)
    ap.add_argument("--skip-qc", action="store_true", help="no Whisper check (not recommended)")
    ap.add_argument("--force-reference", action="store_true")
    ap.add_argument("--whisper", default="large-v3-turbo")
    a = ap.parse_args(argv)
    ecfg = EngineConfig(model=a.model, batch_size=a.batch, fast_predictor=not a.no_fast_predictor,
                        sort=not a.no_sort, trim_ref=not a.no_trim_ref)
    n = Narrator(ecfg, QCConfig(), max_redos=a.max_redos, whisper_model=a.whisper, skip_qc=a.skip_qc,
                 force_reference=a.force_reference)
    session = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "books": []}
    t0 = time.perf_counter()
    for b in a.books:
        r = n.run_book(b, a.out)
        session["books"].append({k: r.get(k) for k in ("book", "finished_audio_min", "raw_xrt_first_pass",
                                                        "end_to_end_xrt", "redos")})
    session["wall_s"] = round(time.perf_counter() - t0, 1)
    try:
        import torch
        session["gpu"] = torch.cuda.get_device_name(0)
    except Exception:
        pass
    audio.dump_json(a.out / "session.json", session)
    log(f"done: {len(a.books)} books in {session['wall_s'] / 60:.1f} min")


if __name__ == "__main__":
    main()
