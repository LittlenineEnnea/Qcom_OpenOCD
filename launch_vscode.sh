#!/usr/bin/env bash
#
# macOS/Linux counterpart of launch_vscode.bat:
#   - put EUD tools on PATH (if EUD_INSTALL_PATH is set)
#   - sync .vscode/tasks.json with the chipsets under scripts/lldb_pkg/target
#   - open the 'openocd' folder as the VS Code workspace
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Same layout the Linux branch of the Qualcomm scripts expects: OPENOCD_DIR=<pkg>/bin
export OPENOCD_DIR="$ROOT/openocd/bin"

if [[ -n "${EUD_INSTALL_PATH:-}" ]]; then
    export PATH="$EUD_INSTALL_PATH:$PATH"
fi

if [[ ! -x "$OPENOCD_DIR/openocd" ]]; then
    echo "error: $OPENOCD_DIR/openocd not found." >&2
    echo "       Run ./build_macos.sh first, or use the package built by the" >&2
    echo "       'Build macOS package' GitHub workflow." >&2
    exit 1
fi

PYTHON="$(command -v python3 || true)"
if [[ -z "$PYTHON" ]]; then
    echo "error: python3 not found" >&2
    exit 1
fi

# Update .vscode/tasks.json from chipsets under scripts/lldb_pkg/target
"$PYTHON" "$ROOT/openocd/scripts/utils/sync_chipset_tasks.py"

# 'code' CLI: use PATH if available, otherwise the one bundled in the app
CODE="$(command -v code || true)"
if [[ -z "$CODE" ]]; then
    for app in "/Applications/Visual Studio Code.app" "$HOME/Applications/Visual Studio Code.app"; do
        if [[ -x "$app/Contents/Resources/app/bin/code" ]]; then
            CODE="$app/Contents/Resources/app/bin/code"
            break
        fi
    done
fi
if [[ -z "$CODE" ]]; then
    echo "error: VS Code 'code' CLI not found" >&2
    exit 1
fi

# Launch VS Code using the 'openocd' folder as the workspace
"$CODE" "$ROOT/openocd"
