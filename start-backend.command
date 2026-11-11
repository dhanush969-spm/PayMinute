#!/bin/bash
# Stops whatever is on port 8000, then starts the PayMinute backend from this folder.
# PayMinute needs Python 3.10 or newer (macOS ships 3.9).
cd "$(dirname "$0")/backend" || exit 1

find_python() {
  for p in python3.13 python3.12 python3.11 python3.10 \
           /opt/homebrew/bin/python3 /usr/local/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/3.1[0-9]/bin/python3 \
           "$HOME"/.pyenv/shims/python3 python3; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
      command -v "$p"; return 0
    fi
  done
  return 1
}

PY=$(find_python)
if [ -z "$PY" ]; then
  echo "Python 3.10+ not found (this Mac has $(python3 --version 2>&1))."
  if command -v brew >/dev/null 2>&1; then
    read -r -p "Install Python 3.12 with Homebrew now? [y/N] " ans
    if [[ "$ans" =~ ^[Yy]$ ]]; then brew install python@3.12 && PY=$(find_python); fi
  fi
  if [ -z "$PY" ]; then
    echo "Install Python 3.12 from https://www.python.org/downloads/macos/ and run this script again."
    exit 1
  fi
fi
echo "Using $PY ($("$PY" --version))"

# rebuild the venv if it was made with an old Python
if [ -x .venv/bin/python ] && ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
  echo "Removing old .venv (Python $(.venv/bin/python --version 2>&1))"
  rm -rf .venv
fi
[ -d .venv ] || "$PY" -m venv .venv
source .venv/bin/activate
python -m pip install -q --upgrade pip
pip install -q -r requirements.txt || { echo "Package install failed."; exit 1; }
pip install -q --no-deps paypal-agent-toolkit==1.11.0 || { echo "Agent Toolkit install failed."; exit 1; }

old=$(lsof -nP -tiTCP:8000 -sTCP:LISTEN)
if [ -n "$old" ]; then
  echo "Stopping old server on port 8000 (PID $old), started in:"
  lsof -a -d cwd -p $old | tail -n +2 | awk '{print "  " $NF}'
  kill $old; sleep 2
fi
if command -v docker >/dev/null 2>&1; then
  ids=$(docker ps -q --filter publish=8000 2>/dev/null)
  [ -n "$ids" ] && echo "Stopping Docker container on port 8000" && docker stop $ids
fi

echo "Starting PayMinute backend on http://localhost:8000"
exec uvicorn app.main:app --reload --port 8000
