#!/bin/bash
# Double-click to write a book. The first run sets everything up (a minute or two).
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed. Download it from https://www.python.org/downloads/ and try again."
  read -r -p "Press Enter to close."
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "Setting up for the first time..."
  python3 -m venv .venv || { read -r -p "Setup failed. Press Enter to close."; exit 1; }
fi
.venv/bin/python -m pip install --quiet --upgrade -r requirements.txt || { read -r -p "Could not install the requirements. Press Enter to close."; exit 1; }

.venv/bin/python write_book.py "$@"
echo
read -r -p "Finished. Press Enter to close."
