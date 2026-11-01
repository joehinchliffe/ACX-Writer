# Narrator: batch audiobook narration on RunPod

This turns manuscripts (Markdown/text, split into chapters) into mastered chapter MP3s
with **Qwen3-TTS 1.7B Base** in voice-clone mode. Every chunk is checked with Whisper
and redone if it fails. Several books share one GPU session.

```bash
export RUNPOD_API_KEY=...                       # runpod.io → Settings → API Keys
python runpod_run.py books/book-one books/book-two --out out/
```

That one command creates a pod, rejects it if it is busy or slow, installs everything,
narrates every book and copies the results home. It terminates the pod before anything
else runs (pass `--after "<upload command>"` for uploads), then logs x-real-time,
redos and cost to `out/runs.jsonl`.

> **Status: built and unit-tested on CPU, not yet measured on a GPU.** The session this was
> written in had no GPU and could not reach Hugging Face or the RunPod API. The speed table
> below gives the **targets from the brief**; the "measured" column stays empty until
> `bench/bench.py` has been run on a 3090/4090 (that takes about 15 minutes, see "Measuring").

## What a book looks like

```
books/my-book/
  book.json            title, author, language, voice, respellings, platform
  voice.wav            clean 20-25 s of the narrator (a voice you have the rights to)
  chapters/01 Start.md one file per chapter ...
  manuscript.md        ... or one file split at "# " headings
  respell.json         optional, merged into book.json "respell"
```

See `examples/example-book/`. In `book.json`, `voice.text` must be the **exact** transcript
of `voice.wav`. A mismatched transcript makes the model ramble and repeat, so the job
checks it with Whisper first and refuses a reference whose WER is over 2%
(`--force-reference` overrides this).

**Languages.** Qwen3-TTS covers English, Chinese, Japanese, Korean, German, French,
Russian, Portuguese, Spanish and Italian. It has **no Dutch** (the job stops with a message
saying so); use another engine for Dutch. Use a native-speaker reference per language:
the built-in preset voices carry a strong English accent in other languages.

**Licence and rights.** Check the Qwen3-TTS model licence on its Hugging Face card before
commercial use. Only clone voices you have written permission to use. ACX also requires
you to disclose AI narration.

## Output

`out/<book>/NN <Chapter title>.mp3` (44.1 kHz, 192 kbps CBR, mono, ID3 tags), plus:

* `report.json`: x-real-time (first pass, and end to end with QC and encoding),
  redo counts, still-failing chunks, and RMS / peak / noise floor per file against the
  platform spec.
* `review/`: any chunk that still failed after 3 redos (the best take is used in the
  MP3), with what Whisper heard. Listen to these before publishing.

The `acx` platform (the default) masters each chapter to -20 dB RMS with a look-ahead
limiter at -3.6 dBFS. It then decodes the MP3 again and checks RMS -23..-18 dB,
peak ≤ -3 dB and noise floor ≤ -60 dB. Each chapter gets 0.75 s of silence at the head and
3 s at the tail.

## Pipeline

| stage | file | notes |
|---|---|---|
| parse + chunk | `narrate/text.py` | Cuts **only** after `. ! ? …` plus optional closing quote(s); never at a bare quote mid-sentence (that made the model invent and repeat phrases). Knows Dr./Mr./No. 5/initials. Packs sentences up to 300 chars within a paragraph. A sentence over 480 chars is split at `; : — ,` as a last resort (and logged). |
| TTS text | `narrate/text.py` | Per-book respellings (`Machiavelli → Mackiavelli`) and numbers to words (years, money, %, ordinals, times, ranges; helplines digit by digit). Applied to the TTS text **only**. |
| generate | `narrate/engine.py` | Batched voice clone; every speed change below is a switch. |
| QC | `narrate/qc.py` | faster-whisper large-v3-turbo, fp16, batched over many chunks via `clip_timestamps`. It compares against the **original** text (WER with compound-word tolerance) and checks length vs expected, internal silences > 2 s, a click at the start, clipping, and hitting the frame cap. Failing chunks are redone together up to 3 times; the best take is kept. `torch.cuda.empty_cache()` runs before Whisper loads (CTranslate2 runs out of memory next to PyTorch's cache otherwise). |
| master + encode | `narrate/audio.py` | Chapters are encoded in parallel with ffmpeg, then measured. |
| cache | `narrate/job.py` | Accepted chunks are saved as FLAC under `out/.work/`, so a rerun skips them. |

## Speed changes (Phase 2)

| # | change | where | target from brief | measured 3090 | measured 4090 |
|---|---|---|---|---|---|
| 0 | stock library, batch 8-24 | `generate_voice_clone` | ~6-7x | _run bench_ | _run bench_ |
| 1 | hand-written code predictor + one CUDA graph | `narrate/fast_predictor.py` | ~18x | | |
| 2 | sort chunks by length | `Engine.synthesize` | ~23x | | |
| 3 | trimmed reference decode (25 frames of context, groups of 8) | `Engine._decode` | VRAM ~20 → ~10 GB | | |
| 4 | batch 48, then 96 (auto step-down on OOM) | `EngineConfig.batch_size` | 45-60x (3090), ~70x (4090) | | |
| 5 | graph the main talker step | not done | next lever | | |

**1. Code predictor.** Profiling (in the brief's runs: 73% of LM time) shows most of the
time in `talker.code_predictor.generate`. That is a 5-layer model predicting 15 residual
codebook tokens for **every** frame through HF `generate()`, where per-step overhead
dominates. `FastCodePredictor` replaces it with:
- a preallocated 16-position KV cache;
- direct calls to the layers (q/k/v projections, q/k RMSNorm, RoPE from the model's own
  rotary table, SDPA with `enable_gqa` exactly as transformers calls it, SwiGLU MLP);
- temperature + top-k sampling on the GPU using the exponential race `argmax(p/E)`,
  E ~ Exp(1). That is the Gumbel-max trick, with the same distribution as
  `torch.multinomial`. top_p stays 1.0 (the model default); top_p < 1 raises an error
  rather than silently differing;
- the whole 15-step loop captured as **one CUDA graph per batch size**.

`bench.py` reports the predictor's share of LM time for every row.

*Exactness.* On CPU, `tests/test_fast_predictor.py` drives the real library classes with
random weights. Greedy codes are **bit-identical** to HF `generate()` over 300 frames at
batch 1, 7 and 24, in fp32 and bf16, and also when run through the library's own
`talker.generate` loop. Sampled distributions for codebooks 1, 2, 8 and 15 are as close to
stock as stock is to itself. On the GPU with the real checkpoint and CUDA graphs on, run
`bench/exactness.py`: it checks greedy end to end, then replays every predictor input from
a sampled run frame by frame, and prints EXACT or NOT EXACT.

**3. Trimmed reference decode.** The library decodes the whole reference (~240 frames,
~19 s) in front of every chunk, then cuts it off proportionally. Here only the last 25
frames go in front (the same left context the library's own `chunked_decode` uses at its
300-frame boundaries), decoded 8 sequences at a time. The decoder returns exactly
`frames × 1920` samples, so cutting `25 × 1920` samples is sample-exact: the same cut the
library's proportional formula gives. That removes the need to pad with silence. Check for
clicks at chunk starts: QC flags them per chunk, and `ab_samples.py` reports them per take.

## Measuring

On any pod after `bash pod/bootstrap.sh` (from this folder):

```bash
.venv/bin/python bench/exactness.py --ref-wav voice.wav --ref-text "exact transcript"
.venv/bin/python bench/bench.py     --ref-wav voice.wav --ref-text "exact transcript"   # ladder 0-7
.venv/bin/python bench/ab_samples.py --ref-wav voice.wav --ref-text "exact transcript" --out ab/
```

`bench.py` runs the ladder on one model load (stock b8/b16/b24 → +fast predictor → +sort
→ +trim → b48 → b96). It records x-real-time (generated audio ÷ wall time for
generation + decode) and peak VRAM for each row, writing `bench/results/<gpu>-<time>.md`
to paste into the table above. Keep a change only if its row beats the row above.
`ab_samples.py` writes stock and fast takes of the same chunks into a blind A/B page
(`ab/index.html`, key in `key.json`) with Whisper WER and click scores for each take.

## RunPod runner details

* Tries RTX 4090 then 3090 on community cloud first. After `--max-rejects` (3) rejected
  community pods it uses Secure Cloud.
* `pod/bootstrap.sh` rejects a pod (exit 3) if nvidia-smi shows load before we start (one
  "3090" had a hidden tenant and ran at half speed), and (exit 4) if a test download runs
  below 25 MB/s (community pods can stall mid-install).
* Setup: a Python 3.11 venv with uv; `qwen-tts soundfile faster-whisper librosa
  hf_transfer`. If the torch build that qwen-tts pulls (CUDA 13) cannot use the host's 12.x
  driver, it reinstalls the **same** torch/torchaudio versions from the PyTorch cu126 index.
  Models are pre-downloaded with `HF_HUB_ENABLE_HF_TRANSFER=1`, then the uv cache (~18 GB)
  is cleaned. Disk: 120 GB.
* All books run in one session. The pod is terminated as soon as the results are copied
  back, and **before** `--after` runs.
* `out/runs.jsonl` holds one line per session: GPU, cloud, $/h, boot and setup minutes,
  job minutes, billed minutes, session cost, $ per finished audio hour, per-book raw and
  end-to-end x-real-time, redo counts, and rejected pods with their reasons.

The RunPod REST calls (`POST/GET/DELETE https://rest.runpod.io/v1/pods`, fields
`gpuTypeIds`, `cloudType`, `containerDiskInGb`, `ports`, `env.PUBLIC_KEY`, `publicIp`,
`portMappings`, `costPerHr`) follow RunPod's v1 REST API as documented. They have **not**
been exercised against the live API from this repo yet; the flow is covered by
`tests/test_runner.py` with a fake API. If a field name has changed, `RunPodAPI` is the
only place to edit. You need `ssh` and `rsync` locally and an SSH key (`~/.ssh/id_ed25519`).

## Tests

```bash
pip install qwen-tts faster-whisper num2words soundfile librosa scipy pytest
python -m pytest -q        # 26 tests, CPU only, ~90 s
```
