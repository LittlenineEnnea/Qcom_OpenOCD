# Attach gdb to an OpenOCD that is already serving APSS core0.
#
#   ./gdb -x apss.gdb                                (from gdb/bin)
#   gdb/bin/gdb -x gdb/bin/apss.gdb                  (from the repo root)
#   ./gdb -ex 'set $apss_port=2000' -x apss.gdb      (other port)
#
# OpenOCD must already be running, e.g. via run.py or launch_vscode.sh.
# The chipset cfgs put APSS core0 on port 3333. gdb/apss.sh starts its own
# OpenOCD on 2000 instead.

set pagination off
set confirm off
set architecture aarch64
# EUD round trips are slow; reads of a few hundred KB take a while
set remotetimeout 30

if $_isvoid($apss_port)
  set $apss_port = 3333
end
eval "target extended-remote 127.0.0.1:%d", $apss_port

# show the instruction at pc every time the core stops
display/i $pc

# dumpmem FILE ADDR LEN  -- save LEN bytes at ADDR to FILE (relative to gdb's cwd)
define dumpmem
  dump binary memory $arg0 $arg1 ($arg1 + $arg2)
  printf "saved 0x%x bytes from 0x%lx to %s\n", $arg2, $arg1, "$arg0"
end
document dumpmem
Save target memory to a raw binary file.
Usage: dumpmem FILE ADDR LEN
Example: dumpmem pbl.bin 0x300000 0x3c000
end

# OpenOCD commands go through "monitor", e.g. "monitor targets", "monitor reg pc"
