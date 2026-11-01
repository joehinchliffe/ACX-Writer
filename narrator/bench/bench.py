"""Phase 1 + 2 measurement ladder on ONE test chapter, one model load.

    python bench/bench.py --ref-wav voice.wav --ref-text "exact transcript" \
        [--chapter bench/sample_chapter.md] [--steps all]

Each step changes ONE thing relative to the previous step; x-real-time
(generated audio seconds / wall seconds, generation + decode, no QC) and
peak VRAM are recorded. Results: bench/results/<gpu>-<time>.json + .md.
Keep a change only if its row is faster than the row above it.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from narrate.engine import Engine, EngineConfig  # noqa: E402
from narrate.text import chunk_chapter, parse_markdown, tts_text  # noqa: E402

# name, engine switches (cumulative ladder)
LADDER = [
    ("stock b8", dict(fast_predictor=False, sort=False, trim_ref=False, batch_size=8)),
    ("stock b16", dict(batch_size=16)),
    ("stock b24", dict(batch_size=24)),
    ("+fast predictor (b24)", dict(fast_predictor=True)),
    ("+sort by length", dict(sort=True)),
    ("+trimmed ref decode", dict(trim_ref=True)),
    ("batch 48", dict(batch_size=48)),
    ("batch 96", dict(batch_size=96)),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref-wav", required=True)
    ap.add_argument("--ref-text", required=True)
    ap.add_argument("--chapter", default=str(Path(__file__).with_name("sample_chapter.md")))
    ap.add_argument("--language", default="english")
    ap.add_argument("--repeat", type=int, default=1, help="repeat the chapter text N times")
    ap.add_argument("--steps", default="all", help="comma list of step indexes, or all")
    ap.add_argument("--seed", type=int, default=1234)
    a = ap.parse_args()

    chapters = parse_markdown(Path(a.chapter).read_text(encoding="utf-8"))
    chunks = [c for ch in chapters for c in chunk_chapter(ch)] * a.repeat
    texts = [tts_text(c.text, "en", {}) for c in chunks]
    print(f"{len(texts)} chunks, {sum(map(len, texts))} characters")

    eng = Engine(EngineConfig(fast_predictor=False, sort=False, trim_ref=False, batch_size=8),
                 profile_predictor=True)
    voice = eng.voice(a.ref_wav, a.ref_text)
    gpu = torch.cuda.get_device_name(0)
    wanted = None if a.steps == "all" else {int(x) for x in a.steps.split(",")}

    # warm-up (kernels, cuBLAS handles) so step 0 is not penalised
    eng.synthesize(texts[:4], voice, a.language)

    rows = []
    for i, (name, sw) in enumerate(LADDER):
        eng.configure(**sw)
        if wanted is not None and i not in wanted:
            continue
        torch.manual_seed(a.seed)
        eng.free_cache()
        _, st = eng.synthesize(texts, voice, a.language)
        # separate profiling pass (CUDA syncs around every predictor call skew timing)
        eng.profiling = True
        _, prof = eng.synthesize(texts[: max(8, len(texts) // 3)], voice, a.language)
        eng.profiling = False
        row = {"step": i, "name": name, **st.as_dict(),
               "predictor_share_of_lm": round(prof.predictor_s / prof.gen_s, 3) if prof.gen_s else None,
               "switches": {k: v for k, v in eng.cfg.__dict__.items() if k != "gen_kwargs"}}
        rows.append(row)
        print(f"{i}: {name:28s} {st.xrt:6.1f}x  peak {st.peak_vram_gb:5.1f} GB  "
              f"predictor {row['predictor_share_of_lm'] or 0:.0%} of LM time  "
              f"(gen {st.gen_s:.0f}s, decode {st.decode_s:.0f}s, audio {st.audio_s:.0f}s)", flush=True)

    out = Path(__file__).with_name("results")
    out.mkdir(exist_ok=True)
    stem = f"{gpu.replace(' ', '_')}-{time.strftime('%Y%m%d-%H%M')}"
    (out / f"{stem}.json").write_text(json.dumps({"gpu": gpu, "torch": torch.__version__, "rows": rows,
                                                  "chunks": len(texts)}, indent=2))
    md = [f"### {gpu} ({len(texts)} chunks)", "", "| # | step | x real time | peak VRAM | predictor share of LM |",
          "|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['step']} | {r['name']} | {r['xrt']:.1f}x | {r['peak_vram_gb']:.1f} GB | "
                  f"{(r['predictor_share_of_lm'] or 0) * 100:.0f}% |")
    (out / f"{stem}.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
