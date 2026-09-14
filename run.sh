#!/usr/bin/env bash
# Quick launcher script for Media Optimizer
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"

# Ensure Homebrew and standard tool paths are accessible for ffmpeg/ffprobe/sips
export PATH="$PATH:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin"

# Intelligent Python binary selector: prefers Apple Silicon native interpreter with both PIL & Tkinter
CANDIDATES=()
if [ -n "$VIRTUAL_ENV" ] && [ -x "$VIRTUAL_ENV/bin/python3" ]; then
    CANDIDATES+=("$VIRTUAL_ENV/bin/python3")
fi

# Prefer Apple Silicon native Homebrew Python binaries
CANDIDATES+=(
    "/opt/homebrew/bin/python3"
    "/opt/homebrew/bin/python3.14"
    "/opt/homebrew/bin/python3.13"
    "/opt/homebrew/bin/python3.12"
    "/opt/homebrew/bin/python3.11"
)

ACTIVE_PY="$(which python3 2>/dev/null)"
if [ -n "$ACTIVE_PY" ]; then
    CANDIDATES+=("$ACTIVE_PY")
fi

CANDIDATES+=(
    "/Library/Frameworks/Python.framework/Versions/Current/bin/python3"
    "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
    "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3"
    "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3"
    "/usr/local/bin/python3"
    "/usr/bin/python3"
)

PYTHON_BIN=""
FALLBACK_BIN=""

for py in "${CANDIDATES[@]}"; do
    if [ -n "$py" ] && [ -x "$py" ]; then
        if "$py" -c "import PIL, tkinter" 2>/dev/null; then
            PYTHON_BIN="$py"
            break
        elif "$py" -c "import PIL" 2>/dev/null && [ -z "$FALLBACK_BIN" ]; then
            FALLBACK_BIN="$py"
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    PYTHON_BIN="$FALLBACK_BIN"
fi

if [ -z "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(which python3)"
fi

if [ -z "$PYTHON_BIN" ] || ! "$PYTHON_BIN" -c "import PIL" 2>/dev/null; then
    echo "Error: Required dependency 'Pillow' is not installed." >&2
    echo "Please install it with: pip3 install pillow" >&2
    exit 1
fi

if [ "$(uname -m)" = "arm64" ] && command -v arch >/dev/null 2>&1; then
    exec arch -arm64 "$PYTHON_BIN" -m media_optimizer.cli "$@"
else
    exec "$PYTHON_BIN" -m media_optimizer.cli "$@"
fi
