#!/usr/bin/env python3
#
# Copyright (c) 2026 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause-Clear
#
# @file sync_chipset_debug_config.py
#
# @section Description
# Generate .vscode/settings.json and .vscode/launch.json from chipset-specific files,
# and ensure CHIPSET in settings.json matches the selected chipset, while preserving
# comments and formatting in settings.json.

# - Expects a chipset name passed as a command-line argument: --chipset <name>
# - Uses 'chipsetLaunchBase' from chipset-specific settings.json if present,
#   otherwise defaults to "scripts/lldb_pkg/target".
# - Copies:
#     <chipsetLaunchBase>/<chipset_lower>/settings.json -> .vscode/settings.json  (if it exists)
#     <chipsetLaunchBase>/<chipset_lower>/launch.json   -> .vscode/launch.json
# - After copying settings.json, updates the CHIPSET value in-place in the text.

import re
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def fail(msg: str, exit_code: int = 1) -> None:
    sys.stderr.write(f"ERROR: {msg}\n")
    sys.exit(exit_code)


def warn(msg: str) -> None:
    sys.stderr.write(f"WARNING: {msg}\n")


def load_json_file_with_comments(path: Path, description: str) -> Any:
    """Load JSON from a file, allowing // comments, and handle errors."""
    if not path.exists():
        fail(f"{description} file not found: {path}")
    if not path.is_file():
        fail(f"{description} path is not a file: {path}")

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as e:
        fail(f"Failed to read {description} ({path}): {e}")

    def strip_line_comment(line: str) -> str:
        result_chars = []
        in_string = False
        escape = False
        i = 0
        while i < len(line):
            ch = line[i]
            if in_string:
                result_chars.append(ch)
                if escape:
                    escape = False
                elif ch == '\\':
                    escape = True
                elif ch == '"':
                    in_string = False
                i += 1
            else:
                if ch == '"':
                    in_string = True
                    result_chars.append(ch)
                    i += 1
                elif ch == '/' and i + 1 < len(line) and line[i + 1] == '/':
                    break
                else:
                    result_chars.append(ch)
                    i += 1
        return ''.join(result_chars)

    cleaned_lines = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        cleaned_line = strip_line_comment(line)
        cleaned_lines.append(cleaned_line)

    cleaned_text = "\n".join(cleaned_lines)

    try:
        return json.loads(cleaned_text)
    except json.JSONDecodeError as e:
        fail(f"Invalid JSON in {description} ({path}): {e}")


def get_repo_root() -> Tuple[Path, Path]:
    """
    Determine repo_root and vscode_dir by walking up from the script
    until a .vscode directory is found.
    """
    script_path = Path(__file__).resolve()
    current = script_path.parent
    repo_root: Optional[Path] = None

    for _ in range(6):  # walk up a few levels
        if (current / ".vscode").exists():
            repo_root = current
            break
        current = current.parent

    if repo_root is None:
        # Fallback: two levels up
        repo_root = script_path.parent.parent

    vscode_dir = repo_root / ".vscode"
    return repo_root, vscode_dir


def read_chipset_launch_base_from_chipset_settings(src_settings: Path) -> str:
    """
    Read chipsetLaunchBase from chipset-specific settings.json if present.
    Falls back to 'scripts/lldb_pkg/target' if not available.
    """
    if not src_settings.exists():
        return "scripts/lldb_pkg/target"

    data = load_json_file_with_comments(src_settings, f"{src_settings} (chipset-specific settings)")
    if not isinstance(data, Dict):
        return "scripts/lldb_pkg/target"

    val = data.get("chipsetLaunchBase", "scripts/lldb_pkg/target")
    if not isinstance(val, str) or not val.strip():
        return "scripts/lldb_pkg/target"
    return val.strip()


def ensure_dir_exists(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        fail(f"Failed to create directory {path}: {e}")


def copy_file(src: Path, dst: Path, description: str) -> None:
    """Copy a file verbatim (no JSON parsing), overwriting dst."""
    if not src.exists():
        fail(f"{description} not found: {src}")
    if not src.is_file():
        fail(f"{description} is not a file: {src}")

    ensure_dir_exists(dst.parent)
    try:
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError as e:
        fail(f"Failed to write {dst} ({description}): {e}")


def copy_launch_json(src: Path, dst: Path) -> None:
    """Copy src JSON file to dst, validating both JSON syntax and contents."""
    data = load_json_file_with_comments(src, "chipset-specific launch.json")

    if not isinstance(data, dict):
        fail(
            f"Expected top-level JSON object in launch file ({src}); "
            f"got {type(data).__name__}"
        )

    version = data.get("version")
    if version != "0.2.0":
        warn(
            f'launch.json "version" is {version!r}, expected "0.2.0". '
            f"VS Code may still accept it, but verify your schema."
        )

    ensure_dir_exists(dst.parent)
    try:
        with dst.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
            f.write("\n")
    except OSError as e:
        fail(f"Failed to write .vscode/launch.json: {e}")


def update_chipset_in_settings_text(settings_path: Path, selected_chipset: str) -> None:
    """
    Update CHIPSET in settings.json by operating on raw text, preserving comments.

    - Looks for a line containing "CHIPSET" : "..."
    - Replaces the value between the quotes with selected_chipset.
    - If CHIPSET is not found, inserts it before the final '}'.
    """
    try:
        text = settings_path.read_text(encoding="utf-8")
    except OSError as e:
        fail(f"Failed to read .vscode/settings.json for CHIPSET update ({settings_path}): {e}")

    # Regex: match "CHIPSET" : "old_value"
    pattern = re.compile(r'("CHIPSET"\s*:\s*")([^"]*)(")')

    def repl(match: re.Match) -> str:
        return match.group(1) + selected_chipset + match.group(3)

    new_text, count = pattern.subn(repl, text, count=1)

    if count == 0:
        # No CHIPSET line found; insert a new line before the final '}'
        insert_line = f'    "CHIPSET": "{selected_chipset}",\n'
        idx = text.rfind("}")
        if idx == -1:
            fail(
                "Could not find closing '}' in settings.json text; "
                "cannot insert CHIPSET line safely."
            )
        new_text = text[:idx] + insert_line + text[idx:]

    try:
        settings_path.write_text(new_text, encoding="utf-8")
    except OSError as e:
        fail(f"Failed to write updated .vscode/settings.json ({settings_path}): {e}")

    print(f"Updated CHIPSET in .vscode/settings.json to '{selected_chipset}' (preserving comments).")


def parse_args_chipset() -> str:
    """Parse --chipset argument from CLI."""
    args = sys.argv[1:]
    if "--chipset" in args:
        idx = args.index("--chipset")
        try:
            chipset = args[idx + 1]
        except IndexError:
            fail("Missing value after --chipset")
        if not chipset.strip():
            fail("Empty chipset value passed to --chipset")
        return chipset.strip()
    fail("No --chipset argument provided. Configure the task to pass one via ${input:chipsetInput}.")


def main() -> None:
    repo_root, vscode_dir = get_repo_root()

    # 1. Get chipset from CLI
    selected_chipset = parse_args_chipset()
    print(f"Selected chipset from task input: {selected_chipset}")

    chipset_lower = selected_chipset.strip().lower()

    # 2. Determine chipset base directory (from chipset-specific settings if available)
    default_base = "scripts/lldb_pkg/target"
    chipset_root = repo_root / default_base / chipset_lower
    src_settings = chipset_root / "settings.json"

    if src_settings.exists():
        chipset_launch_base = read_chipset_launch_base_from_chipset_settings(src_settings)
    else:
        chipset_launch_base = default_base

    print(f"Using chipsetLaunchBase: {chipset_launch_base!r}")

    chipset_dir = repo_root / chipset_launch_base / chipset_lower
    src_settings = chipset_dir / "settings.json"
    src_launch = chipset_dir / "launch.json"

    dst_settings = vscode_dir / "settings.json"
    dst_launch = vscode_dir / "launch.json"

    # 3. Copy chipset-specific settings.json -> .vscode/settings.json (if it exists)
    if src_settings.exists():
        print(
            f"Found chipset-specific settings.json at {src_settings.relative_to(repo_root)}; "
            f"copying to .vscode/settings.json."
        )
        copy_file(src_settings, dst_settings, "chipset-specific settings.json")
    else:
        warn(
            f"No chipset-specific settings.json found at {src_settings}; "
            f"will only update CHIPSET in existing .vscode/settings.json if present."
        )

    # 4. Ensure CHIPSET in .vscode/settings.json matches the selected chipset (preserving comments)
    if dst_settings.exists():
        update_chipset_in_settings_text(dst_settings, selected_chipset)
    else:
        warn(
            "No .vscode/settings.json found after copy; "
            "CHIPSET cannot be updated."
        )

    # 5. Copy chipset-specific launch.json -> .vscode/launch.json (with chipset marker check)
    if not src_launch.exists():
        fail(
            f"Chipset-specific launch.json not found:\n"
            f"  {src_launch}\n"
            f"Check that:\n"
            f"  - Directory exists: {chipset_launch_base}/{chipset_lower}\n"
            f"  - File exists: launch.json in that directory."
        )

    print(
        f"Using launch.json from chipset '{chipset_lower}' "
        f"({src_launch.relative_to(repo_root)})"
    )

    if not dst_launch.exists():
        print(
            f".vscode/launch.json does not exist; creating it "
            f"from chipset-specific launch.json."
        )
        copy_launch_json(src_launch, dst_launch)
        print(f"Successfully wrote {dst_launch.relative_to(repo_root)}")
        return

    existing = load_json_file_with_comments(dst_launch, "existing .vscode/launch.json")
    chipset_data = load_json_file_with_comments(src_launch, "chipset-specific launch.json")

    existing_chipset = existing.get("chipset")
    src_chipset = chipset_data.get("chipset")

    existing_chipset_norm = (
        existing_chipset.strip().lower() if isinstance(existing_chipset, str) else None
    )
    src_chipset_norm = (
        src_chipset.strip().lower() if isinstance(src_chipset, str) else None
    )

    if src_chipset_norm is None:
        warn(
            f"Chipset-specific launch.json ({src_launch}) has no 'chipset' field; "
            f"consider adding \"chipset\": \"{selected_chipset}\" at the top-level."
        )

    if (
        existing_chipset_norm is not None
        and src_chipset_norm is not None
        and existing_chipset_norm == src_chipset_norm
    ):
        print(
            f".vscode/launch.json already marked for chipset "
            f"'{existing_chipset}'; no changes made."
        )
        return

    print(
        ".vscode/launch.json is not marked for the selected chipset "
        "or chipset does not match; overwriting with the new chipset "
        "configuration."
    )
    copy_launch_json(src_launch, dst_launch)
    print(f"Successfully updated {dst_launch.relative_to(repo_root)}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        fail("Interrupted by user", exit_code=130)