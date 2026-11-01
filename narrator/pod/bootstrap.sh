#!/usr/bin/env bash
# Bootstrap a RunPod (or any Linux + NVIDIA) machine for narration.
#
#   bash pod/bootstrap.sh            # run from the narrator/ folder
#
# Exit codes the runner understands:
#   3  GPU already shows load (hidden tenant) -> reject this pod
#   4  download speed below MIN_MBPS (default 25 MB/s) -> reject this pod
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
MIN_MBPS=${MIN_MBPS:-25}
SPEED_URL=${SPEED_URL:-https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo/resolve/main/model.bin}
TTS_MODEL=${TTS_MODEL:-Qwen/Qwen3-TTS-12Hz-1.7B-Base}
TTS_TOKENIZER=${TTS_TOKENIZER:-Qwen/Qwen3-TTS-Tokenizer-12Hz}
WHISPER_REPO=${WHISPER_REPO:-mobiuslabsgmbh/faster-whisper-large-v3-turbo}
export HF_HOME=${HF_HOME:-/workspace/hf}
export HF_HUB_ENABLE_HF_TRANSFER=1
say() { echo "[bootstrap $(date +%H:%M:%S)] $*"; }

# ---------------------------------------------------------------- 1. idle GPU?
say "checking the GPU is idle"
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
busy=0
for i in 1 2 3 4 5; do
  read -r util mem <<<"$(nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits | head -1 | tr -d ',')"
  if [ "${util:-0}" -gt 5 ] || [ "${mem:-0}" -gt 600 ]; then busy=$((busy+1)); fi
  sleep 1
done
if [ "$busy" -ge 2 ]; then
  say "REJECT: GPU shows load before we started (util ${util}%, ${mem} MiB used)"; exit 3
fi

# ------------------------------------------------------------ 2. network speed
say "measuring download speed"
bytes_per_s=$(curl -sL --max-time 15 -r 0-314572799 -o /dev/null -w '%{speed_download}' "$SPEED_URL" || echo 0)
mbps=$(python3 -c "print(int(float('${bytes_per_s}')/1e6))")
say "download: ${mbps} MB/s"
if [ "$mbps" -lt "$MIN_MBPS" ]; then
  say "REJECT: download below ${MIN_MBPS} MB/s"; exit 4
fi

# ------------------------------------------------------------- 3. system deps
if ! command -v ffmpeg >/dev/null; then
  say "installing ffmpeg"
  (apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg sox >/dev/null) \
    || say "apt ffmpeg failed; will use imageio-ffmpeg"
fi
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
  export PATH="$HOME/.local/bin:$PATH"
fi

# --------------------------------------------------------- 4. python packages
say "creating Python 3.11 venv"
uv venv -q -p 3.11 .venv
export VIRTUAL_ENV=$ROOT/.venv PATH=$ROOT/.venv/bin:$PATH
uv pip install -q -r pod/requirements.txt

# qwen-tts may pull a torch build for a CUDA newer than the host driver. If CUDA
# does not work, reinstall the SAME torch/torchaudio versions from the cu126 index.
if ! python -c "import torch,sys; torch.zeros(1).cuda(); sys.exit(0)" 2>/dev/null; then
  TV=$(python -c "import torch;print(torch.__version__.split('+')[0])")
  AV=$(python -c "import torchaudio;print(torchaudio.__version__.split('+')[0])")
  for CU in cu126 cu128; do
    say "torch $TV cannot use this driver; reinstalling torch==$TV torchaudio==$AV from $CU"
    if uv pip install -q --reinstall "torch==$TV" "torchaudio==$AV" \
         --index-url "https://download.pytorch.org/whl/$CU"; then
      python -c "import torch; torch.zeros(1).cuda()" && break
    fi
  done
fi
python - <<'EOF'
import torch
print(f"[bootstrap] torch {torch.__version__} CUDA {torch.version.cuda} on {torch.cuda.get_device_name(0)}")
torch.zeros(1).cuda()
EOF

# ----------------------------------------------------------- 5. models + cache
say "downloading models"
hf download "$TTS_MODEL" >/dev/null
hf download "$TTS_TOKENIZER" >/dev/null
hf download "$WHISPER_REPO" >/dev/null
uv cache clean -q || true          # ~18 GB otherwise
df -h "$ROOT" | tail -1
say "ready"
