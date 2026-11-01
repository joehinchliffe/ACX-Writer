"""Prove the fast code predictor is exact on the real checkpoint (GPU, CUDA graphs on).

1. Greedy end to end: talker + predictor greedy, stock vs fast -> codes must be
   bit-identical.
2. Frame by frame: every (B, 2, H) input the stock predictor saw during a
   SAMPLED run is replayed through the graphed fast predictor with greedy
   decoding and compared with the stock predictor (greedy) on the same input.

    python bench/exactness.py --ref-wav voice.wav --ref-text "exact transcript"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from narrate import fast_predictor  # noqa: E402
from narrate.engine import Engine, EngineConfig  # noqa: E402

TEXTS = [
    "The river had been rising for three days, and nobody in the village wanted to say it out loud.",
    "She folded the letter twice, put it in her pocket, and walked to the station without looking back.",
    "Habits are not built in a single morning; they are built in a hundred ordinary ones.",
    "Why do we keep promises to strangers more easily than promises to ourselves?",
]
GREEDY = dict(do_sample=False, subtalker_dosample=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref-wav", required=True)
    ap.add_argument("--ref-text", required=True)
    ap.add_argument("--language", default="english")
    a = ap.parse_args()

    eng = Engine(EngineConfig(fast_predictor=False, sort=False, trim_ref=False, gen_kwargs=GREEDY))
    voice = eng.voice(a.ref_wav, a.ref_text)
    cp = eng.inner.talker.code_predictor

    # --- 1. greedy end to end
    stock = eng._codes(TEXTS, voice, a.language, cap=600)
    fast = fast_predictor.install(eng.tts)
    got = eng._codes(TEXTS, voice, a.language, cap=600)
    frames = sum(c.shape[0] for c in stock)
    same = len(stock) == len(got) and all(torch.equal(x, y) for x, y in zip(stock, got))
    print(f"[1] greedy end to end: {frames} frames, bit-identical: {same}")

    # --- 2. frame by frame on inputs from a sampled run
    fast_predictor.uninstall(eng.tts)
    seen = []
    stock_gen = cp.generate

    def record(*args, **kw):
        seen.append(kw["inputs_embeds"].detach().clone())
        return stock_gen(*args, **kw)
    cp.generate = record
    eng.cfg.gen_kwargs = {}
    eng._codes(TEXTS, voice, a.language, cap=600)
    cp.__dict__.pop("generate", None)
    fast = fast_predictor.FastCodePredictor(cp, use_cuda_graph=True)
    n_frames = mism = 0
    with torch.inference_mode():
        for emb in seen:
            ref = cp.generate(inputs_embeds=emb, max_new_tokens=15, do_sample=False, top_p=1.0, top_k=50,
                              temperature=0.9, output_hidden_states=True,
                              return_dict_in_generate=True).sequences
            out = fast.generate(inputs_embeds=emb, max_new_tokens=15, do_sample=False).sequences
            n_frames += emb.shape[0]
            mism += int((ref != out).any(dim=1).sum())
    print(f"[2] frame by frame: {n_frames} frames, {mism} mismatching frames")
    ok = same and mism == 0
    print("EXACT" if ok else "NOT EXACT - do not use the fast predictor on this setup")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
