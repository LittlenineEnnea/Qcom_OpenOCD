#!/usr/bin/env bash
#
# macOS/Linux counterpart of launch_lldb.bat: start an interactive LLDB in
# openocd/scripts with its local .lldbinit (which imports openocd_lldb.py).
# Uses the LLDB bundled with the CodeLLDB VS Code extension (Python 3.12
# with telnetlib), falling back to lldb on PATH.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export OPENOCD_DIR="$ROOT/openocd/bin"

if [[ -n "${EUD_INSTALL_PATH:-}" ]]; then
    export PATH="$EUD_INSTALL_PATH:$PATH"
fi

LLDB="$(ls -d "$HOME"/.vscode/extensions/vadimcn.vscode-lldb-*/lldb/bin/lldb 2>/dev/null | sort -V | tail -n 1 || true)"
if [[ -z "$LLDB" ]]; then
    LLDB="$(command -v lldb || true)"
fi
if [[ -z "$LLDB" ]]; then
    echo "error: lldb not found (install the CodeLLDB VS Code extension)" >&2
    exit 1
fi

cd "$ROOT/openocd/scripts"
exec "$LLDB" --local-lldbinit
