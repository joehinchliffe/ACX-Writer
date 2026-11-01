"""A/B samples: the same chunks through the stock library and the fast path.

    python bench/ab_samples.py --ref-wav voice.wav --ref-text "exact transcript" --out ab/

Writes ab/NN_stock.wav, ab/NN_fast.wav, ab/index.html (players side by side,
order shuffled per row so the listener does not know which is which; the key
is in ab/key.json) and the Whisper word error rate of each take.
Sampling is random, so takes differ like two readings by the same narrator;
listen for voice, accent, pacing, clicks at the start and artefacts.
"""
from __future__ import annotations

import argparse
import html
import json
import random
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from narrate import audio  # noqa: E402
from narrate.engine import Engine, EngineConfig  # noqa: E402
from narrate.qc import QCConfig, WhisperChecker, judge  # noqa: E402
from narrate.text import chunk_chapter, parse_markdown, tts_text  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref-wav", required=True)
    ap.add_argument("--ref-text", required=True)
    ap.add_argument("--chapter", default=str(Path(__file__).with_name("sample_chapter.md")))
    ap.add_argument("--language", default="english")
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--out", type=Path, default=Path("ab"))
    a = ap.parse_args()

    chunks = [c for ch in parse_markdown(Path(a.chapter).read_text()) for c in chunk_chapter(ch)]
    chunks = [c for c in chunks if len(c.text) > 60][: a.n]
    texts = [tts_text(c.text, "en", {}) for c in chunks]

    eng = Engine(EngineConfig(fast_predictor=False, sort=False, trim_ref=False, batch_size=len(texts)))
    voice = eng.voice(a.ref_wav, a.ref_text)
    torch.manual_seed(1)
    stock, _ = eng.synthesize(texts, voice, a.language)
    eng.configure(fast_predictor=True, sort=True, trim_ref=True, batch_size=96)
    torch.manual_seed(1)
    fast, _ = eng.synthesize(texts, voice, a.language)

    eng.free_cache()
    w = WhisperChecker()
    hs = w.transcribe_many([t.wav for t in stock], eng.sr, "en")
    hf = w.transcribe_many([t.wav for t in fast], eng.sr, "en")

    a.out.mkdir(parents=True, exist_ok=True)
    rows, key = [], {}
    rng = random.Random(7)
    for i, c in enumerate(chunks):
        for tag, take, hyp in (("stock", stock[i], hs[i]), ("fast", fast[i], hf[i])):
            audio.write_wav(a.out / f"{i:02d}_{tag}.wav", take.wav, eng.sr)
        vs = judge(c.text, hs[i], stock[i].wav, eng.sr, "en", QCConfig())
        vf = judge(c.text, hf[i], fast[i].wav, eng.sr, "en", QCConfig())
        pair = [("stock", vs), ("fast", vf)]
        rng.shuffle(pair)
        key[f"{i:02d}"] = {"A": pair[0][0], "B": pair[1][0], "wer_stock": round(vs.wer, 3),
                           "wer_fast": round(vf.wer, 3), "click_stock": round(vs.audio["start_click"], 1),
                           "click_fast": round(vf.audio["start_click"], 1)}
        cells = "".join(f'<td>{lab}<br><audio controls preload="none" src="{i:02d}_{t}.wav"></audio></td>'
                        for lab, (t, _) in zip("AB", pair))
        rows.append(f"<tr><td>{i:02d}</td><td>{html.escape(c.text)}</td>{cells}</tr>")
    (a.out / "key.json").write_text(json.dumps(key, indent=2))
    (a.out / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>A/B narration</title>"
        "<style>body{font:15px system-ui;margin:16px}td{border-top:1px solid #ccc;padding:6px;vertical-align:top}"
        "</style><h1>Stock vs fast: which is which?</h1><p>The key is in key.json.</p>"
        "<table>" + "".join(rows) + "</table>")
    ws = sum(v["wer_stock"] for v in key.values()) / len(key)
    wf = sum(v["wer_fast"] for v in key.values()) / len(key)
    print(f"mean WER stock {ws:.3f}  fast {wf:.3f}  -> {a.out}/index.html")


if __name__ == "__main__":
    main()
