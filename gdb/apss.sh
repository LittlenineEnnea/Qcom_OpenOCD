#!/usr/bin/env bash
#
# Debug APSS core0 with gdb over EUD SWD.
#
# Starts OpenOCD with the chipset config, serving APSS core0 on GDB_PORT
# (default 2000), then attaches gdb to it. OpenOCD is stopped when gdb exits.
#
#   gdb/apss.sh [elf-with-symbols]
#
# Environment overrides:
#   CHIPSET   chipset dir under openocd/scripts/lldb_pkg/target  (default: kodiak)
#   ADAPTER   interface cfg under openocd/config/interface         (default: eud_swd)
#   GDB_PORT  gdb port for APSS core0                              (default: 2000)
#   BIND      address OpenOCD listens on                           (default: 127.0.0.1)
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"

CHIPSET="${CHIPSET:-kodiak}"
ADAPTER="${ADAPTER:-eud_swd}"
GDB_PORT="${GDB_PORT:-2000}"
BIND="${BIND:-127.0.0.1}"

OPENOCD="$ROOT/openocd/bin/openocd"
GDB="$HERE/bin/gdb"
IFACE_CFG="$ROOT/openocd/config/interface/$ADAPTER.cfg"
TARGET_CFG="$ROOT/openocd/scripts/lldb_pkg/target/$CHIPSET/$CHIPSET.cfg"
TARGET_NAME="$(echo "$CHIPSET" | tr '[:lower:]' '[:upper:]').apss.core0"
LOG="${TMPDIR:-/tmp}/openocd-apss-$GDB_PORT.log"

for f in "$OPENOCD" "$GDB" "$IFACE_CFG" "$TARGET_CFG"; do
    [[ -e "$f" ]] || { echo "error: missing $f (run ./build_macos.sh first)" >&2; exit 1; }
done

if nc -z 127.0.0.1 "$GDB_PORT" 2>/dev/null; then
    echo "error: port $GDB_PORT is already in use (another OpenOCD running?)" >&2
    exit 1
fi

echo "Starting OpenOCD: $CHIPSET via $ADAPTER, APSS core0 on $BIND:$GDB_PORT (log: $LOG)"
"$OPENOCD" -f "$IFACE_CFG" -f "$TARGET_CFG" \
    -c "bindto $BIND" \
    -c "$TARGET_NAME configure -gdb-port $GDB_PORT" \
    >"$LOG" 2>&1 &
OCD_PID=$!
trap 'kill "$OCD_PID" 2>/dev/null || true; wait "$OCD_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 150); do
    if nc -z 127.0.0.1 "$GDB_PORT" 2>/dev/null; then
        break
    fi
    if ! kill -0 "$OCD_PID" 2>/dev/null; then
        echo "error: OpenOCD exited during startup:" >&2
        tail -n 20 "$LOG" >&2
        exit 1
    fi
    sleep 0.2
done
if ! nc -z 127.0.0.1 "$GDB_PORT" 2>/dev/null; then
    echo "error: OpenOCD did not open port $GDB_PORT within 30 s:" >&2
    tail -n 20 "$LOG" >&2
    exit 1
fi

"$GDB" -q \
    -ex "set architecture aarch64" \
    -ex "set remotetimeout 30" \
    -ex "target extended-remote 127.0.0.1:$GDB_PORT" \
    "$@"
