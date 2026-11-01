#!/usr/bin/env bash
# Everything in one go, for running by hand on a GPU pod:
#
#   bash pod/try_it.sh examples/example-book
#
# 1. sets the machine up (skips this if already done)
# 2. checks the fast mode gives the same result as the normal library
# 3. measures the speed of each step
# 4. narrates the book into out/
set -uo pipefail
cd "$(dirname "$0")/.."
BOOK=${1:-examples/example-book}
step() { echo; echo "=================== $* ==================="; }

if [ ! -f "$BOOK/book.json" ]; then echo "No book.json in $BOOK"; exit 1; fi

if [ ! -x .venv/bin/python ]; then
  step "1/4 Setting up the machine (10-15 minutes)"
  bash pod/bootstrap.sh
  code=$?
  if [ $code -eq 3 ] || [ $code -eq 4 ]; then
    echo; echo ">>> This pod is no good (busy or slow). Terminate it on runpod.io and rent another one."; exit $code
  elif [ $code -ne 0 ]; then
    echo; echo ">>> Setup failed. Copy the red/last lines above and send them to Claude."; exit $code
  fi
fi
PY=.venv/bin/python

WAV=$($PY -c "import json,sys,os;b=json.load(open('$BOOK/book.json'));print(os.path.join('$BOOK',b['voice']['wav']))")
TEXT=$($PY -c "import json;print(json.load(open('$BOOK/book.json'))['voice']['text'])")
if [ ! -f "$WAV" ]; then echo ">>> Put your voice recording at $WAV first."; exit 1; fi

step "2/4 Checking fast mode is exact (a few minutes)"
EXTRA=""
if ! $PY bench/exactness.py --ref-wav "$WAV" --ref-text "$TEXT"; then
  echo ">>> Fast mode did not match. Using the slower normal mode for the book."
  EXTRA="--no-fast-predictor"
fi

step "3/4 Measuring speed (about 15 minutes)"
$PY bench/bench.py --ref-wav "$WAV" --ref-text "$TEXT" || echo ">>> Speed test failed (the book will still be tried)."

step "4/4 Narrating the book"
$PY -m narrate.job "$BOOK" --out out $EXTRA
code=$?

mkdir -p out && cp -r bench/results out/speed_results 2>/dev/null
$PY -c "import shutil; shutil.make_archive('results', 'zip', 'out')"
echo
if [ $code -eq 0 ]; then
  echo ">>> Done. Download narrator/results.zip (right-click it on the left, Download), then TERMINATE the pod."
else
  echo ">>> The book step failed. Copy the last lines above to Claude. Then TERMINATE the pod."
fi
exit $code
