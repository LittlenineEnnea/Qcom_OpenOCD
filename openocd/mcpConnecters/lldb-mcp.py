#!/usr/bin/env python3
#
# Copyright (c) 2026 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file lldb-mcp.py
#
# @section Description
# MCP stdio server that proxies lldb_command calls to LLDB's TCP MCP server.
# Each tool call opens a fresh TCP connection to LLDB, performs the MCP
# handshake, runs the command, and closes. One instance runs per debug
# configuration, distinguished by port number.
#
# Architecture:
#   QGenie LLM  <--stdio MCP-->  THIS SCRIPT  <--TCP MCP-->  LLDB :<port>
#
# Usage:
#   python lldb-mcp.py [--port PORT]
#
#   --port PORT   TCP port the LLDB MCP server is listening on (default: 59999).
#                 Each debug configuration uses a different port so multiple
#                 sessions can run simultaneously.

import sys
import io
import json
import re
import socket
import os
import argparse
import atexit
from datetime import datetime

# Windows stdout default is \r\n — MCP requires \n
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, newline="\n", line_buffering=True)
    sys.stdin  = io.TextIOWrapper(sys.stdin.buffer,  newline="\n")

def _parse_args():
    """!
    Parse command-line arguments.

    @return     - parsed arguments namespace with port attribute

    """
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--port", type=int, default=59999)
    args, _ = p.parse_known_args()
    return args

_args = _parse_args()

LLDB_HOST = "127.0.0.1"
LLDB_PORT = _args.port

def _lldb_reachable():
    """!
    Check whether LLDB's MCP TCP port is accepting connections.

    @return     - True if the port is reachable, False otherwise

    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect((LLDB_HOST, LLDB_PORT))
        s.close()
        return True
    except OSError:
        return False

_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(_log_dir, exist_ok=True)
LOG_FILE = os.path.join(_log_dir, f"lldb-mcp-{LLDB_PORT}.log")
_log_fh = open(LOG_FILE, "a", buffering=1, encoding="utf-8")

def _cleanup():
    """!
    Flush a shutdown message and close the log file handle on exit.

    Registered with atexit so it runs on both clean exit and
    KeyboardInterrupt/EOF termination.

    """
    _log("Proxy shutting down")
    _log_fh.close()

atexit.register(_cleanup)

def _log(msg):
    """!
    Write a timestamped log entry to both stderr and the log file.

    @param msg  - message string to log

    """
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts}] {msg}"
    print(line, file=sys.stderr, flush=True)
    print(line, file=_log_fh, flush=True)

# ---------------------------------------------------------------------------
# One-shot LLDB call: connect, handshake, run command, disconnect
# ---------------------------------------------------------------------------

def lldb_call(command, timeout=15):
    """!
    Open a one-shot TCP connection to LLDB, run a single command, and return
    its text output.

    Performs the full MCP handshake on every call: initialize → tools/list →
    debugger_list (to locate an active target) → tools/call. The connection is
    closed on return regardless of success or failure.

    @param command  - LLDB command string to execute
    @param timeout  - socket timeout in seconds (default: 15)

    @return         - text output returned by LLDB for the command

    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((LLDB_HOST, LLDB_PORT))

        def send(obj):
            s.sendall((json.dumps(obj) + "\n").encode("utf-8"))

        def recv_msg():
            buf = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    raise RuntimeError("LLDB closed the connection unexpectedly")
                buf += chunk
                if b"\n" in buf:
                    line, _ = buf.split(b"\n", 1)
                    return json.loads(line.decode("utf-8").strip())

        # initialize
        send({
            "jsonrpc": "2.0", "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "lldb-mcp-proxy", "version": "0.2"}
            }
        })
        init_resp = recv_msg()
        if "error" in init_resp:
            raise RuntimeError(f"MCP initialize failed: {init_resp['error']}")

        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        # tools/list — confirm server ready
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        recv_msg()

        # find active debugger
        send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": "debugger_list", "arguments": {}}})
        dl_resp = recv_msg()
        text = dl_resp.get("result", {}).get("content", [{}])[0].get("text", "")
        uris = re.findall(r"lldb-mcp://debugger/\S+", text)

        active_debugger = None
        for uri in uris:
            send({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                  "params": {"name": "command", "arguments": {"debugger": uri, "command": "process status"}}})
            probe = recv_msg()
            probe_text = probe.get("result", {}).get("content", [{}])[0].get("text", "")
            if "invalid target" not in probe_text.lower():
                active_debugger = uri
                break

        if active_debugger is None:
            raise RuntimeError(
                "No active debug target found.\n\n"
                "Select your debug configuration from the Run and Debug dropdown (Ctrl+Shift+D) "
                "and press F5, then wait for the session to attach before retrying."
            )

        # run command
        send({
            "jsonrpc": "2.0", "id": 5,
            "method": "tools/call",
            "params": {
                "name": "command",
                "arguments": {"debugger": active_debugger, "command": command}
            }
        })
        call_resp = recv_msg()
        if "error" in call_resp:
            raise RuntimeError(f"command failed: {call_resp['error']}")

        result = call_resp.get("result", {})
        parts  = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        return "\n".join(parts) if parts else json.dumps(result)

    finally:
        try:
            s.close()
        except Exception as e:
            _log(f"socket close failed: {e}")


# ---------------------------------------------------------------------------
# Lazy help cache — fetched from live LLDB on first tools/list call
# ---------------------------------------------------------------------------

_help_cache = None   # None = not fetched yet, "" = fetched but unavailable

def _fetch_help():
    """!
    Fetch the LLDB help text from a live session and cache it.

    Called lazily on the first tools/list request. If LLDB is not yet running
    the cache is set to an empty string so the server still responds; the next
    tools/list call will retry.

    @return     - LLDB help text string, or empty string if unavailable

    """
    global _help_cache
    if _help_cache is not None:
        return _help_cache
    try:
        raw = lldb_call("help", timeout=5)
        # Keep only the top-level command listing — stop at the first blank line
        # after the header so we don't flood the description with full help text.
        lines = []
        for line in raw.splitlines():
            lines.append(line)
            if len(lines) > 120:   # hard cap to keep description reasonable
                lines.append("  ... (run 'help' for the full list)")
                break
        _help_cache = "\n".join(lines)
    except Exception as e:
        _log(f"_fetch_help failed (LLDB not running yet?): {e}")
        _help_cache = ""   # LLDB not running yet — will retry next tools/list
        return ""
    return _help_cache


BASE_DESCRIPTION = (
    "Run any LLDB debugger command against the live debug session and return its output.\n"
    "Use this tool to inspect program state: call stacks, variables, memory, registers,\n"
    "threads, disassembly, breakpoints, and more.\n\n"
    "MANDATORY: You MUST start every response that uses this tool with a line in this exact format:\n"
    "  Running: `<the exact command>`\n"
    "Do this BEFORE the tool call, every single time, no exceptions. Never summarize or paraphrase —\n"
    "show the exact string you are passing as the 'command' argument.\n\n"
    "Run 'help' to list all commands. Run 'help <command>' for details on a specific command.\n"
    "Custom openocd_lldb commands (debug, debug_adapter, set_chipset, binary_path, etc.)\n"
    "are also registered — run 'help' to see them all.\n"
)


def _build_tool():
    """!
    Build the MCP tool descriptor for lldb_command.

    Appends the cached LLDB help listing to the base description when
    available, so the LLM has visibility into all available commands.

    @return     - tool descriptor dict suitable for a tools/list response

    """
    help_text = _fetch_help()
    description = BASE_DESCRIPTION
    if help_text:
        description += "\n--- Available commands (live from LLDB 'help') ---\n" + help_text

    return {
        "name": "lldb_command",
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The LLDB command, exactly as typed in the LLDB console."
                }
            },
            "required": ["command"]
        }
    }


# ---------------------------------------------------------------------------
# MCP stdio server
# ---------------------------------------------------------------------------

def handle(req):
    """!
    Dispatch a single MCP JSON-RPC request and return the response object.

    Handles initialize, notifications/initialized, tools/list, resources/list,
    and tools/call (lldb_command). Unknown methods return a -32601 error.

    @param req  - decoded JSON-RPC request dict

    @return     - JSON-RPC response dict, or None for notifications

    """
    method = req.get("method", "")
    rid    = req.get("id")
    params = req.get("params", {})

    def ok(result):
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def err(code, msg):
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}}

    if method == "initialize":
        return ok({
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "lldb-mcp-proxy", "version": "0.2"}
        })

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        if not _lldb_reachable():
            _log(f"tools/list: LLDB not reachable on port {LLDB_PORT}, returning empty tool list")
            return ok({"tools": []})
        # Re-check if LLDB came up since last call (clears "" sentinel so it retries)
        # Note: Global state mutation is safe in this single-threaded implementation.
        # If concurrent request handling is added in the future, implement proper
        # synchronization (e.g., threading.Lock) to protect _help_cache access.
        global _help_cache
        if _help_cache == "":
            _help_cache = None
        return ok({"tools": [_build_tool()]})

    if method == "resources/list":
        return ok({"resources": []})

    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments", {})

        if name != "lldb_command":
            return err(-32601, f"Unknown tool: {name}")

        command = args.get("command", "")

        try:
            _log(f">>> {command}")
            output = lldb_call(command)
            _log(f"<<< {output[:200].rstrip()}{'...' if len(output) > 200 else ''}")
            display = f"$ {command}\n{'-'*40}\n{output}"
            return ok({"content": [{"type": "text", "text": display}]})
        except ConnectionRefusedError:
            msg = (
                f"Cannot connect to LLDB on {LLDB_HOST}:{LLDB_PORT}.\n\n"
                "The debug session is not running or the MCP server has not started yet.\n"
                "Select your debug configuration and press F5, then check the Debug Console for:\n"
                "  'MCP server started with connection listeners: ...'"
            )
            _log(f"ConnectionRefusedError on port {LLDB_PORT}")
            return ok({"content": [{"type": "text", "text": msg}], "isError": True})
        except Exception as e:
            _log(f"tools/call error: {e}")
            return ok({"content": [{"type": "text", "text": str(e)}], "isError": True})

    return err(-32601, f"Method not found: {method}")


def main():
    """!
    Entry point — run the MCP stdio server loop.

    Reads JSON-RPC requests line by line from stdin, dispatches each to
    handle(), and writes responses to stdout. Exits cleanly on EOF or
    KeyboardInterrupt.

    """
    _log(f"Started — connecting to {LLDB_HOST}:{LLDB_PORT} per call, log: {LOG_FILE}")

    while True:
        try:
            line = sys.stdin.readline()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            _log(f"JSON decode error: {e} — input: {line[:100]}")
            continue
        resp = handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
