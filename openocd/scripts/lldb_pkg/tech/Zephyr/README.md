# Zephyr LLDB Debugging Scripts

This directory contains LLDB scripts for debugging Zephyr RTOS applications.

## Quick Start

The Zephyr debugging commands are automatically registered when you load the main OpenOCD LLDB script:

```bash
(lldb) command script import openocd/scripts/openocd_lldb.py
```

This will load all debugging commands including the Zephyr RTOS utilities.

## Usage

### View available subcommands
```bash
(lldb) zephyr
```

### Run specific commands
```bash
(lldb) zephyr threads       # Display all threads
(lldb) zephyr mutexes       # Display all mutexes
(lldb) zephyr semaphores    # Display all semaphores
(lldb) zephyr mboxes        # Display all mailboxes
(lldb) zephyr msgqs         # Display all message queues
(lldb) zephyr pipes         # Display all pipes
(lldb) zephyr stacks        # Display all stacks
(lldb) zephyr timers        # Display all timers
(lldb) zephyr mem_slabs     # Display all memory slabs
```

## Files

- `zephyr.py` - All Zephyr debugging functions (threads, mutexes, semaphores, timers, stacks, msgqs, mboxes, pipes, mem_slabs)
- `README.md` - This file

## Requirements

- Zephyr must be built with `CONFIG_OBJECT_TRACING=y` for object tracking to work
- Debug symbols must be available in the binary

## Troubleshooting

### "Could not find _track_list_k_* symbol"
This means object tracking is not enabled. Add to your `prj.conf`:
```
CONFIG_OBJECT_TRACING=y
```

## Example Session

```bash
# Start LLDB with your Zephyr binary
$ lldb zephyr.elf

# Load the OpenOCD debugging tools (includes Zephyr commands)
(lldb) command script import openocd/scripts/openocd_lldb.py

# View all threads
(lldb) zephyr threads

# View all mailboxes
(lldb) zephyr mboxes

# View all mutexes
(lldb) zephyr mutexes
```
