#!/usr/bin/env python3
#
# Copyright (c) 2026 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file sync_chipset_tasks.py
#
# @section Description
# Safely update .vscode/tasks.json based on directories under scripts/lldb_pkg/target.

# - Preserves leading // header comments exactly as they are.
# - Lists all subdirectories of scripts/lldb_pkg/target as chipsets.
# - If .vscode/tasks.json is valid JSON (after stripping // comments in the JSON section):
#     * adds/updates 'chipsetInput' pickString (options = chipsets),
#       but only overrides options if they don't already match the discovered chipsets
#     * adds/updates 'Set Active Chipset' task
#     * preserves all other tasks (e.g. 'Start OpenOCD')
# - If .vscode/tasks.json cannot be parsed as JSON:
#     * prints an error and DOES NOT modify the file.
# - No default chipset is set in the input (no "default" field).

import json
from pathlib import Path
from typing import List, Dict, Any, Optional


def fail(msg: str) -> None:
    print(f"ERROR: {msg}")


def discover_chipsets(repo_root: Path, chipset_launch_base: str = "scripts/lldb_pkg/target") -> List[str]:
    base_dir = repo_root / chipset_launch_base
    if not base_dir.exists() or not base_dir.is_dir():
        fail(f"Chipset base directory not found or not a directory: {base_dir}")
        return []

    chipsets = sorted([p.name for p in base_dir.iterdir() if p.is_dir()])
    if not chipsets:
        fail(f"No chipset directories found under {base_dir}")
    return chipsets


def split_header_and_json(text: str) -> (str, str):
    """
    Split tasks.json text into:
    - header: leading lines that start with // (and blank lines)
    - json_part: the rest (starting from the first non-comment, non-blank line)

    The header will be preserved verbatim; only json_part is parsed/rewritten.
    """
    lines = text.splitlines(keepends=True)
    header_lines: List[str] = []
    json_lines: List[str] = []

    in_header = True
    for line in lines:
        stripped = line.lstrip()
        if in_header and (stripped.startswith("//") or stripped == "" or stripped == "\n" or stripped == "\r\n"):
            header_lines.append(line)
        else:
            in_header = False
            json_lines.append(line)

    header_text = "".join(header_lines)
    json_text = "".join(json_lines)
    return header_text, json_text


def parse_json_with_line_comments(text: str, description: str) -> Optional[Dict[str, Any]]:
    """
    Strip // comments from a JSONC-like string and parse as JSON.

    - Skips full-line // comments
    - Removes inline // comments outside of strings
    """
    lines = text.splitlines()
    cleaned_lines: List[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("//"):
            continue

        result = ""
        in_string = False
        escape = False

        i = 0
        while i < len(line):
            ch = line[i]
            if in_string:
                result += ch
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                i += 1
            else:
                if ch == '"':
                    in_string = True
                    result += ch
                    i += 1
                elif ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
                    # start of // comment outside string -> ignore rest
                    break
                else:
                    result += ch
                    i += 1

        cleaned_lines.append(result)

    cleaned_text = "\n".join(cleaned_lines)

    try:
        data = json.loads(cleaned_text)
    except json.JSONDecodeError as e:
        fail(f"Failed to parse {description} as JSON after stripping comments: {e}")
        return None

    if not isinstance(data, dict):
        fail(f"{description} is not a JSON object; refusing to modify it.")
        return None

    return data


def load_tasks_json_with_header(tasks_path: Path) -> Optional[Dict[str, Any]]:
    """
    Load tasks.json text, split header and JSON, strip // comments from JSON,
    and parse as JSON. Returns a dict or None on error.

    Header is preserved separately.
    """
    if not tasks_path.exists():
        # If the file doesn't exist, start with a minimal structure (no header)
        return {"version": "2.0.0", "tasks": []}

    try:
        text = tasks_path.read_text(encoding="utf-8")
    except OSError as e:
        fail(f"Failed to read existing tasks.json ({tasks_path}): {e}")
        return None

    header_text, json_text = split_header_and_json(text)
    data = parse_json_with_line_comments(json_text, str(tasks_path))
    if data is None:
        return None

    # Attach header_text to data for later reassembly
    data["_header_text"] = header_text
    return data


def chipset_options_match(existing_options: Any, chipsets: List[str]) -> bool:
    """
    Check if existing_options (from tasks.json) matches chipsets exactly,
    ignoring order, but requiring the same set of strings.
    """
    if not isinstance(existing_options, list):
        return False
    existing = sorted(
        [str(opt).strip() for opt in existing_options if isinstance(opt, str)]
    )
    expected = sorted([c.strip() for c in chipsets])
    return existing == expected


def merge_chipset_input(data: Dict[str, Any], chipsets: List[str]) -> None:
    """
    Add or update the 'chipsetInput' entry in data['inputs'].

    If all chipsets are already listed in 'chipsetInput.options', do NOT override.
    Otherwise, replace 'chipsetInput.options' with the new list.

    No default chipset is set (no "default" field).
    """
    inputs = data.get("inputs")
    if not isinstance(inputs, list):
        inputs = []
        data["inputs"] = inputs

    existing_idx = None
    existing_input = None
    for idx, inp in enumerate(inputs):
        if isinstance(inp, dict) and inp.get("id") == "chipsetInput":
            existing_idx = idx
            existing_input = inp
            break

    new_input = {
        "id": "chipsetInput",
        "type": "pickString",
        "description": "Select chipset",
        "options": chipsets
    }

    if existing_idx is not None and isinstance(existing_input, dict):
        existing_options = existing_input.get("options")
        if chipset_options_match(existing_options, chipsets):
            print("chipsetInput options already match discovered chipsets; leaving as-is.")
            return
        else:
            inputs[existing_idx] = new_input
            print("chipsetInput options differ from discovered chipsets; updating options.")
    else:
        inputs.append(new_input)
        print("chipsetInput not found; adding new input with discovered chipsets.")


def merge_chipset_task(data: Dict[str, Any]) -> None:
    """
    Add or update the 'Set Active Chipset' task,
    preserving all other tasks.
    """
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        tasks = []
        data["tasks"] = tasks

    existing_idx = None
    for idx, task in enumerate(tasks):
        if (
            isinstance(task, dict)
            and task.get("label") == "Set Active Chipset"
        ):
            existing_idx = idx
            break

    new_task = {
        "label": "Set Active Chipset",
        "type": "shell",
        "command": "python",
        "osx": {
            "command": "python3"
        },
        "args": [
            "scripts/utils/sync_chipset_debug_config.py",
            "--chipset",
            "${input:chipsetInput}"
        ],
        "options": {
            "cwd": "${workspaceFolder}"
        },
        "problemMatcher": []
    }

    if existing_idx is not None:
        tasks[existing_idx] = new_task
        print("Updated existing 'Set Active Chipset' task.")
    else:
        tasks.append(new_task)
        print("Added new 'Set Active Chipset' task.")


def main() -> None:
    script_path = Path(__file__).resolve()
    # Assumes this file is under <repo_root>/scripts/utils/
    repo_root = script_path.parent.parent.parent

    vscode_dir = repo_root / ".vscode"
    tasks_path = vscode_dir / "tasks.json"

    chipsets = discover_chipsets(repo_root)
    if not chipsets:
        # Nothing to do; tasks.json remains unchanged
        return

    # Load existing tasks.json (with comment support & header preservation)
    tasks_data = load_tasks_json_with_header(tasks_path)
    if tasks_data is None:
        # Parsing failed; leave tasks.json untouched
        return

    header_text = tasks_data.pop("_header_text", "")

    # Ensure version is set
    if "version" not in tasks_data:
        tasks_data["version"] = "2.0.0"

    # Merge chipset input and task
    merge_chipset_input(tasks_data, chipsets)
    merge_chipset_task(tasks_data)

    # Serialize JSON and reattach header
    try:
        json_text = json.dumps(tasks_data, indent=4)
        json_text += "\n"
    except TypeError as e:
        fail(f"Failed to serialize tasks.json: {e}")
        return

    final_text = header_text + json_text

    vscode_dir.mkdir(parents=True, exist_ok=True)
    try:
        tasks_path.write_text(final_text, encoding="utf-8")
        print(
            f"Updated {tasks_path} with chipsets: {', '.join(chipsets)} "
            f"and preserved existing tasks and header comments."
        )
    except OSError as e:
        fail(f"Failed to write {tasks_path}: {e}")


if __name__ == "__main__":
    main()