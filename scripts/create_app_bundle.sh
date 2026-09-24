#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
APP_NAME="Media Optimizer"
APP_DIR="$ROOT_DIR/$APP_NAME.app"

echo "Creating macOS application bundle: $APP_DIR"

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"

# Create Info.plist
cat <<EOF > "$APP_DIR/Contents/Info.plist"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>MediaOptimizer</string>
    <key>CFBundleIdentifier</key>
    <string>com.local.mediaoptimizer</string>
    <key>CFBundleName</key>
    <string>Media Optimizer</string>
    <key>CFBundleDisplayName</key>
    <string>Media Optimizer</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSArchitecturePriority</key>
    <array>
        <string>arm64</string>
    </array>
    <key>LSRequiresNativeExecution</key>
    <true/>
</dict>
</plist>
EOF

# Copy codebase into Resources for full portability
cp -r "$ROOT_DIR/media_optimizer" "$APP_DIR/Contents/Resources/"

# Create executable launcher script
cat <<'EOF' > "$APP_DIR/Contents/MacOS/MediaOptimizer"
#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUNDLE_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
RES_DIR="$(cd "$SCRIPT_DIR/../Resources" && pwd)"

if [ -d "$RES_DIR/media_optimizer" ]; then
    export PYTHONPATH="$RES_DIR:$PYTHONPATH"
fi
if [ -d "$BUNDLE_DIR/media_optimizer" ]; then
    export PYTHONPATH="$BUNDLE_DIR:$PYTHONPATH"
fi

export PATH="$PATH:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin"

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
        if "$py" -c "import PIL, tkinter, pillow_heif" 2>/dev/null; then
            PYTHON_BIN="$py"
            break
        elif "$py" -c "import PIL, tkinter" 2>/dev/null && [ -z "$FALLBACK_BIN" ]; then
            FALLBACK_BIN="$py"
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
    if command -v osascript >/dev/null 2>&1; then
        osascript -e 'display alert "Media Optimizer Setup Required" message "Media Optimizer requires Python with Pillow and pillow-heif libraries.\n\nPlease install dependencies in Terminal:\nbrew install ffmpeg exiftool python\npip3 install -r requirements.txt" as critical'
    fi
    echo "Error: Python 3 with Pillow is required." >&2
    exit 1
fi

# Log output when launched without terminal (e.g. double clicked in Finder)
if [ ! -t 1 ]; then
    exec > /tmp/media_optimizer_app.log 2>&1
fi

if [ "$(uname -m)" = "arm64" ] && command -v arch >/dev/null 2>&1; then
    exec arch -arm64 "$PYTHON_BIN" -m media_optimizer.gui "$@"
else
    exec "$PYTHON_BIN" -m media_optimizer.gui "$@"
fi
EOF

chmod +x "$APP_DIR/Contents/MacOS/MediaOptimizer"
chmod +x "$APP_DIR"

echo "Application bundle created successfully at: $APP_DIR"
