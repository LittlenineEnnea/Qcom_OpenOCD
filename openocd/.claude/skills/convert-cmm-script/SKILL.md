---
name: convert-cmm-script
description: Convert a Lauterbach/T32 .cmm debug script (e.g. from cmm_scripts/) into an OpenOCD+LLDB Python module under scripts/lldb_pkg/, following this repo's existing command patterns. Use for requests like "convert <script>.cmm", "port this cmm script to lldb", or "make <script>.cmm work with openocd". The person asking is typically NOT familiar with T32 syntax or the LLDB SDK — this skill supplies both.
---

# Converting a T32 .cmm script to OpenOCD/LLDB Python

Don't ask the user to explain T32 constructs — look them up in the mapping
table below (§1). Default to the existing repo patterns instead of
inventing new ones.

## 0. Classify the script first

Read the whole `.cmm` file, then decide which known case it matches —
**this determines the whole approach**, so don't skip it. These are the
two common shapes seen so far, not an exhaustive list — a script may
combine them, or contain something neither describes (e.g. an interactive
TUI/window-management framework, with `wa_*`/window-position subroutines
and a command-history REPL loop). If a script — or part of one — doesn't
fit either case, don't force it into Path A or B: propose how to handle
that part (e.g. drop a T32-only TUI shell and keep the underlying
read/write logic as a plain one-shot command) and confirm with the user
before converting it.

- **Register/bitfield table.** The file is pure data: `y.create.l NAME
  <addr-expr>` (named address) and `y.create.macro NAME(val) (<expr>)`
  (bitfield extractor), plus scalar constants (`&x=12.`) and static arrays
  (`v.a \arr[i]="..."`). No `GOSUB`, no pointer casts, no live struct
  walking.
  → Go to **Path A**.
- **Struct-walking dump script.** The file has `GOSUB`/`RETURN` subroutines
  that cast raw addresses to C struct pointers and chase `->next` pointers
  through live target memory (`V.VALUE(((some_struct *)&ptr)->next)`,
  `DATA.STRING(D:V.VALUE(...))`), and/or does register peeks
  (`PER.SET.FIELD`, `DATA.SET`) and formatted output (`AREA.*`, `PRINT`,
  `WRITE #1`).
  → Go to **Path B**.

Mixed files exist — some scripts dispatch to a separate Path-A target file
(one per chip/SoC) for register data — convert each half by its own path
and wire them together the same way the `.cmm` did (see the `DO`/
import-dispatch row in the mapping table).

**A dynamic `DO`/dispatch to a per-chip or otherwise dependent `.cmm` is a
required companion conversion, not optional follow-up work** — this applies
even if the user's request only names the top-level script (e.g. "convert
BCMDump.cmm") and says nothing about the file(s) it `DO`s. Trace every `DO
<path-expr>` in the file, including one built at runtime from a
`string.scanandextract`'d argument (e.g. `DO
"&(my_path)/&(soc_id).cmm"`), before calling the port done:
- If the dispatch target is a thin wrapper with no data/logic of its own
  (parses an arg, builds a path, `DO`s a deeper file, catches errors —
  like `bcm_targets/BCMTarget.cmm`) — fold its dispatch+error-catch logic
  into the caller's dynamic-import (`importlib.import_module` +
  `try/except ImportError`) rather than giving it its own Python file.
- If the dispatch target selects between multiple concrete per-chip/per-SoC
  files (e.g. `bcm_targets/<soc>.cmm`, one per chip) and the user's request
  didn't say which chip(s), **stop and ask** which chip(s) to convert —
  don't guess, and don't hand back a caller module (e.g. `BCMDump.py`)
  whose dynamic import has no corresponding chip module behind it yet.
  Call this out explicitly rather than silently leaving it unconverted.

## 1. T32 → OpenOCD/LLDB mapping reference

See also: [T32 to LLDB command map](https://qualcomm-confluence.atlassian.net/wiki/spaces/CTP/pages/460366855/T32+to+LLDB+command+map)
for rows marked "Confluence" below.

**Register/memory access**

| T32 | LLDB / Python | Source |
|---|---|---|
| `PER.SET.FIELD`/`DATA.SET &access_mode <addr> <val> [mask]`, `Data.Long(addr)` | `dap_access -r/-w` → `dapAccessGDB(debugger, command, result, "Yes", addr_or_None, None, write_reg_or_None, value, mask)` | `utils/dap_access.py` |
| `d.dump EZAXI:<addr>` / `Data.set EZAXI:<addr> <data>` (secure) | `dap_access -r <addr>` / `dap_access -w <addr> -v <value>` | Confluence |
| `Data.List`/`d.dump <addr>` (non-secure) | `memory read <addr>` | Confluence |
| `Data.Set <addr> <value>` (non-secure) | `memory write <addr> <value>` | Confluence |
| `data.byte`/`data.word` (narrow read) | `memory read -s1/-s2 <addr>` | LLDB |
| Unformatted `V.VALUE(...)`/`&val` printed directly (no `FORMAT.HEX`/`FORMAT.DECIMAL` wrapper) | still format as hex in Python (`f"0x{val:X}"`) | T32's default numeric display radix is hex, so an unformatted value prints in hex on a real T32 session even though the `.cmm` source never calls `FORMAT.HEX` — check the *actual T32 output* for that line, not just whether the `.cmm` line has a `FORMAT.HEX` call, before deciding a value should print as plain decimal |
| `d.save.binary "path" ezaxi:ADDR++SIZE` | `memory read --outfile <path> --binary <start> <end>` | LLDB |
| `d.load.binary &file &addr /noclear` | `process.WriteMemory(addr, open(file,"rb").read(), err)` | SB-API, no repo precedent |

**Struct/symbol walking** (see step 2 for the symbols-present/absent decision)

| T32 | LLDB / Python | Source |
|---|---|---|
| `V.VALUE(((T *)&ptr)->field)` | See §2 below (symbols-present/absent decision) — do not skip straight to a mapping here | `tech/QuRT/OSAM/task_list.py`, `utils.py` |
| `DATA.STRING(D:V.VALUE(...))`, `v.str(...)`/`v.string(...)` | `read_memory_cstring(process, logger, addr, length)` | `tech/QuRT/OSAM/utils.py` |
| `y.fe "GOSUB X *" npa` (foreach linked list) | Python `while node != 0:` walking `->next`, reading each node via the row above | pattern, no builtin |
| `symbol.foreach`/`Y.FOREACH "GOSUB X *" sym` (foreach all instances of a symbol) | loop over `target.FindGlobalVariables(name, UINT32_MAX)` | SB-API, no repo precedent |
| `y.exist(&sym)`, `y.count(sym)`, `y.type(sym)` | `target.FindFirstGlobalVariable(name).IsValid()`, `len(target.FindGlobalVariables(...))`, `.GetType()` | SB-API, no repo precedent |
| `y.FUNCTION(P:&ptr)` | `target.ResolveSymbolContextForAddress(addr, ...).GetSymbol().GetName()` | SB-API, no repo precedent |

**Breakpoints/execution**

| T32 | LLDB / Python | Source |
|---|---|---|
| `Break.Set <function_name>` / `b.set`/`b.s` | `breakpoint set --name <fn>` → `target.BreakpointCreateByName(fn)` | Confluence + `image/xbl.py`, `image/uefi.py` |
| `Break.Set <address>` | `breakpoint set --address <addr>` → `target.BreakpointCreateByAddress(addr)` | Confluence |
| `Break.Set "<file>" <line>` | `breakpoint set --file <file> --line <line>` → `target.BreakpointCreateByLocation(file, line)` | Confluence |
| `Break.Delete ALL` / `b.delete /all` | `breakpoint delete` → `debugger.HandleCommand("br del")` | Confluence + `image/xbl.py` |
| `Go`/`g` (resume) | `continue`/`c` → `debugger.HandleCommand("c")` | Confluence + `image/xbl.py`, `image/uefi.py` |
| `Go` (first run) | `process launch`/`run` | Confluence |
| `Step`/`Step.In`/`Step.Out` | `thread step-over`(`next`) / `step-in`(`step`) / `step-out`(`finish`) | Confluence |
| `wait !run()`, `STATE.RUN()` | `process.GetState() == lldb.eStateStopped`, then `thread.GetStopReason() == lldb.eStopReasonBreakpoint` | `image/xbl.py` |
| (Nth-hit check on a breakpoint) | `bp_obj.GetHitCount()` | `image/xbl.py` |
| (auto-run commands on breakpoint hit) | `bkpt.SetCommandLineCommands(lldb.SBStringList())` | `image/uefi.py` |
| `SYStem.Up` | `process plugin packet monitor reset` | Confluence |
| `SYStem.Attach` | `gdb-remote <port>` | Confluence |
| `SYStem.Down`, `sys.d`, `sys.u` | `process detach` | Confluence |

**Module/image loading & paths**

| T32 | LLDB / Python | Source |
|---|---|---|
| `data.load.elf <path> /nocode /noclear /strippart /sourcepath` | `target.AddModule(path, None, None, dSYM_path)` + `target.SetModuleLoadAddress(module, offset)` | `image/uefi.py::runtimedxeUEFI` |
| `y.spath.srd+= <path>` | `settings append target.source-map <remote> <local>` — use `append`, not the `set` shown on Confluence (repo uses `append`, matches T32's `+=`) | `image/uefi.py` |
| image path lookup scripts | `image_path -i <subsystem> [-s <path>]` (project command; use `/` not `\`) | Confluence |
| `Data.List <function_name>` | `list <function_name>` | Confluence |

**Output/file I/O**

| T32 | LLDB / Python | Source |
|---|---|---|
| `AREA.CREATE`/`AREA.SELECT`/`PRINT` | `logger.info(...)` | `utils/debug_context.py` |
| `WRITE #1 ...` | plain `open(path, "w")`. Collapse T32's `&dump` indirection into one `emit(msg)` closure chosen once up front | pattern |
| `OPEN #4 ... /CREATE` ... `WRITE #4` ... `CLOSE #4` (multiple channels) | one Python file handle per logical channel, not one `emit()` | pattern |
| `os.command`, `OS.window python ...`, `wp.OS.window <perl cmd>` + `printer.file` | `subprocess.run([...])` — if the shelled-out script is already Python, prefer porting its logic in-process instead of re-shelling | pattern |

**Control flow / args / subroutines**

| T32 | LLDB / Python | Source |
|---|---|---|
| `ENTRY %line &args` + `string.scanandextract("&args","key=","default")` | `shlex.split(command)` + `optparse`/`argparse` | `tech/pmic/pmic_dump.py::pmic_dump` |
| `ENTRY %LINE &var` (capture whole line) | `argparse` with `nargs=REMAINDER`, or `command.split(None, N)` | pattern |
| `ON ERROR GOSUB`/`ON ERROR GOTO <label>`/`ERROR.RESet` | Python `try/except` | pattern |
| `GOSUB label &args` / `RETURN` / `RETURNVALUES &x` (global-side-effect convention) | Python method call / `return value` — one method per `GOSUB` label, snake_cased | pattern |
| `PARAMETERS &v` / `RETURN <expr>` / `RETURNVALUES &v1 &v2` / `PRIVATE &v` (functional-subroutine convention) | ordinary Python function returning a tuple; `PRIVATE` → local variable | pattern |
| `DO <other.cmm> "args"` (dispatch to a separate per-chip/per-target file) | `importlib.import_module(f"lldb_pkg.tech.NOC.bcm_targets.{chip_name}")`, mirroring `import_pmic_config_modules` | `tech/pmic/pmic_dump.py` |
| `y.create.l NAME <addr-expr>`, `y.create.macro NAME(val) (<expr>)`, `y.create.done` | `NAME = 0x...` / `def NAME(val): return <expr>` / no-op terminator — see **Path A** | pattern |
| Registering the script as a T32 `do` command | (a) `optparse`-based command class appended to `openocd_lldb.py` (see `pmic_dump`/`meta_dir` classes), (b) `register_command('name', func)` from `tech/QuRT/OSAM/command_registry.py` dispatched via `qurt <subcommand>`, or (c) no command at all — a standalone script that reads `sys.argv`/runs unconditionally at module scope, invoked via `command script import <file>.py` or the repo's `runscript <path> [args...]` command (see `runpyscript` in `openocd_lldb.py`) — ask the user which they want if the script's own arguments (or lack of them) don't make it obvious; see Path B step 6. **If the user says not to register a command in `openocd_lldb.py` (or an equivalent instruction), always use (c)** — do not add a command class or an import line to `openocd_lldb.py`, regardless of how command-like the script's own arguments look. | pattern |

**Custom project commands** (no T32 equivalent, already implemented here)

| Command | Use |
|---|---|
| `pmic_peek_poke <-r/-w> <busID> <slaveId> <addr> [<data>]` | e.g. `pmic_peek_poke -r 0 0 0x105` |
| `pmic_dump <bus_id> <slave_id>` | dumps PMIC registers to XML |
| `stub <function_name>` | bypass a function during live debug |

## Unresolved — no confirmed LLDB equivalent yet

These constructs aren't on the Confluence page and have no repo precedent.
Don't guess a mapping — flag them to the user instead of writing
speculative code:

- `sys.CPU <core_name>` — selects a core by name before a dump; unclear if
  this maps to GDB-remote core-select, `thread select`, or an OpenOCD
  monitor command.
- `sys.option.imaskhll on` / `imaskasm on` / `enreset on` — T32 host-side
  stepping/interrupt-mask options; unclear whether LLDB needs an equivalent
  or these can be dropped as no-ops.
- `y.reset` — resets T32's symbol/macro cache; likely a no-op under LLDB
  but unconfirmed.

## 2. Struct field access — check symbols before writing anything

For every C struct type referenced in the `.cmm`, determine whether the
loaded target image has DWARF debug info for it, in an LLDB session
attached to a representative target:

```
(lldb) script print(lldb.debugger.GetSelectedTarget().FindFirstType("some_struct"))
```

- **Type found (symbols present):** use the symbolic path, exactly like
  `lldb_pkg/tech/QuRT/OSAM/task_list.py` does:
  `target.FindFirstType("T")` → `raw_read_memory(process, logger, addr,
  type.GetByteSize())` → `create_value_from_data(target, logger, name, data,
  type)` → `value.GetChildMemberWithName("field")`. Use
  `get_child_value_err_raise` / `get_child_value_as_uint32` from
  `lldb_pkg/tech/QuRT/OSAM/utils.py` rather than re-deriving null checks.
- **Type not found (no symbols):** do NOT guess field offsets. Stop and ask
  the user for the struct layout (a header file, or a confirmed
  offset table) before writing any field-access code. Once you have it, use
  `read_memory_uint32`/`raw_read_memory` from the same `utils.py` with the
  explicit offsets, and leave a comment citing where the layout came from.

This decision is per-script, sometimes per-struct — don't assume the whole
file falls one way just because one type resolved.

## Path A — register/bitfield table files

Convert directly, one T32 construct at a time, using the mapping table above
— no separate generator, no fixed output format. This is a mechanical but
manual conversion, so the person doing it (or reviewing it) can adjust
naming, grouping, or add comments as they see fit for their chip:

- `y.create.l NAME <addr-expr>` → `NAME = 0x...` (evaluate the address
  expression yourself — drop the `&(access_view)` view prefix, resolve any
  `&offset`-style scalar, and note that T32 decimal literals end in `.`,
  e.g. `22.` is just `22`)
- `y.create.macro NAME(val) (<expr>)` → `def NAME(val): return <expr>`
  (same expression, just strip the trailing `.` off decimal literals)
- Scalar constants (`&x=12.`) → `X = 12`
- Static arrays (`v.a \arr[i]="..."` / `v.a \arr[i][j]=...`) → a plain
  Python list or list-of-lists literal, indexed the same way
- `IF ("&access"=="aop")` / `IF (simulator())` branches that swap in an
  alternate address view — convert the default (non-aop, non-simulator)
  branch unless the team member tells you otherwise; call out what was
  skipped in a short comment so it's not a silent gap

Place the result under `lldb_pkg/tech/NOC/bcm_targets/<chip>.py` (one file
per input `.cmm`). Verify by diffing addresses/macros against the `.cmm`
source line-by-line; this doesn't require T32 knowledge, just matching
numbers.

## Path B — struct-walking dump scripts

Follow the `TcbList` class shape from `lldb_pkg/tech/QuRT/OSAM/task_list.py`:

1. A class (e.g. `SomeDump`) with `__init__(self, debugger)` that grabs
   `target = debugger.GetSelectedTarget()` and `process =
   target.GetProcess()`.
2. One method per T32 `GOSUB` label, same responsibility, snake_cased name
   (`DumpResourceState` → `dump_resource_state`). Preserve the original
   call graph — don't restructure it, this is a mechanical port.
3. If the script dispatches to a per-chip target file (`bcm_targets/*.cmm`),
   dynamically import the Path-A generated module for
   `g_dbgContext.chipsetName`, mirroring `import_pmic_config_modules` in
   `pmic_dump.py`. **If the port is invoked via `runscript` (see step 6) and
   does its own `importlib.import_module("lldb_pkg...")`, don't assume
   `lldb_pkg` is already importable** — `runscript` execs the file's raw
   text (`exec(open(sys.argv[0]).read())` in `runpyscript`), so there's no
   package context and `__file__` is undefined; `lldb_pkg` only resolves at
   all because `.lldbinit` happened to add the scripts root to `sys.path` at
   session start, which is env-dependent, not guaranteed. Add a small
   `sys.path` fixup at the top of the dynamic-import function that derives
   the scripts root from `sys.argv[0]` (not `__file__`) and inserts it if
   missing, so the script works regardless of the caller's cwd or how the
   session was launched — see `_ensure_scripts_root_on_path` in
   `tech/BCM/BCMDump.py`.
4. Argument parsing via `shlex.split(command)` + `optparse`/`argparse`
   instead of `string.scanandextract`.
5. Output via `logger.info(...)`, with an optional file-write path if an
   `op=<dir>` argument is given — don't try to replicate T32's `AREA`
   windowing.
6. At module bottom, register the command per the mapping table's last row
   — or, if the source `.cmm` ran immediately on `do <script>` with no
   argument parsing worth preserving, call the top-level function
   unconditionally at module scope instead, so `command script import
   <file>.py` runs the dump immediately, matching T32's `do <script>`
   behavior. Ask the user which shape they want if unclear — don't default
   to command registration just because it's more common. **If the user
   asks you not to register a command in `openocd_lldb.py`, treat that as a
   hard constraint: never add a command class or an import line there for
   this script, even if it takes arguments** — instead read arguments via
   `sys.argv[1:]` (with `argparse`/`optparse`) so the script works when run
   via the repo's `runscript <path> [args...]` command (see `runpyscript` in
   `openocd_lldb.py`) or plain `command script import <file>.py`.
7. Place the new file under `lldb_pkg/tech/NOC/` (new package, mirroring
   `lldb_pkg/tech/QuRT/OSAM/` and `lldb_pkg/tech/pmic/`) — add an empty
   `__init__.py` if the package doesn't exist yet.

## 3. Chipset-specific conversions — template pattern (optional, for multi-variant scripts)

**Only apply this section if converting a script that has multiple chipset-specific variants** (e.g., `std_loadbuild_la.cmm` exists for Kaanapali, Mavros, Clarence, etc.). For single-chipset conversions, skip to the output checklist.

When converting a script with chipset variants, follow the **template + override pattern**:

1. **Identify the base template** — the most complete/canonical version (e.g., Kaanapali for `std_loadbuild_la.cmm`). This becomes the structural reference.
2. **Identify chipset-specific content** — buildroots, binaries, search paths, LOADIMG groups, engineering overrides, verification checks. These differ per chipset.
3. **Create the chipset module** by:
   - Copying the base template's structure and function signatures exactly
   - Replacing only the chipset-specific data (buildroots, binaries, etc.)
   - Keeping all helper functions, class patterns, and logic identical
   - Using the same import structure and error handling
4. **Verify completeness** — count buildroots, binaries, LOADIMG groups, and engineering overrides against the original `.cmm` source. Spot-check a few values to ensure they match.
5. **Test the conversion** — if possible, run the LLDB command (`std_loadbuild_la_<chipset> VERIFY`) to confirm all variables resolve correctly.

**Key insight:** When multiple chipset variants exist, use the most complete one as the template (not the simplest), then adapt it down if needed. This ensures no features are accidentally omitted.

## 4. Multi-file conversions — avoid manual reorganization (optional, for complex scripts)

**Only apply this section if the conversion creates duplicate methods or requires reorganizing large code sections**. For straightforward conversions, skip to the output checklist.

When converting a script that spans multiple files or creates structural issues:

1. **Use a template-based approach** — copy the reference implementation (e.g., Kaanapali) as a structural template, then populate with chipset-specific data.
2. **Avoid manual method reorganization** — if the output has methods in a different order than the reference, don't try to reorder them manually via sequential edits. Instead:
   - Identify all methods that need to be moved
   - Use a complete rewrite (copy template + populate) rather than incremental edits
   - This prevents duplicate methods and file corruption
3. **Verify no duplicates** — after conversion, check: `grep "^def \|^class " <file> | sort | uniq -d` should return nothing.
4. **Compare line counts** — if the converted file is significantly larger/smaller than expected, investigate before calling it done. Larger is usually OK (more buildroots/binaries), but smaller may indicate missing content.

**Key insight:** Manual sequential edits to reorganize large code sections are error-prone and create duplicates. A complete rewrite from a template is faster and safer.


## Output checklist

Before calling the port done, confirm:

- [ ] File header matches repo convention: SPDX line, `@file`, `@section
  Description` (copy the docstring style from `task_list.py` or
  `pmic_dump.py`, not `enable_all_clocks.py`'s older style).
- [ ] No leftover T32 syntax or comments — `;` comments become `#`, no
  `ENTRY`/`GOSUB`/`&var` left anywhere.
- [ ] Path A: every `y.create.l`/`y.create.macro`/scalar/array line has a
  corresponding Python line with the address, bit-shift/mask arithmetic, or
  table entry matching the `.cmm` source exactly — spot-check by diffing
  values, not just skimming.
- [ ] Path B only — Imports use the repo's `try/except` fallback for
  `lldb_pkg....` vs bare module import — every existing OSAM file does this;
  copy it verbatim.
- [ ] Path B only — Command registered (either `command_registry` or an
  `openocd_lldb.py` command class), importable from `openocd_lldb.py`'s
  import block, *or* — if built as a standalone run-on-import/`runscript`
  script, or if the user said not to register a command in
  `openocd_lldb.py` — no command class and no new import line were added to
  `openocd_lldb.py`; the module-scope call at the bottom runs the whole dump
  reading args from `sys.argv` (or with no args needed).
- [ ] Path B only — If registered as a command, `help <command>` (or `qurt
  <subcommand>`) renders sensible usage text. Standalone scripts skip this.
- [ ] Path B only — Struct field access followed step 2 above — no invented
  offsets.
- [ ] Any value the `.cmm` prints unformatted (no `FORMAT.HEX`/
  `FORMAT.DECIMAL` wrapper) was checked against actual T32 output, not just
  the `.cmm` source, before choosing decimal — T32's default radix is hex,
  so these usually need `f"0x{val:X}"` in Python even with no `FORMAT.HEX`
  call in sight.
- [ ] Path B only, if the script does its own `importlib.import_module` and
  runs via `runscript` — the dynamic-import function fixes up `sys.path`
  from `sys.argv[0]` (not `__file__`, which is undefined under `runscript`'s
  `exec`) before importing, so it doesn't silently depend on the caller's
  cwd or how the LLDB session happened to start. Verify by exec'ing the file
  with `cwd` set somewhere unrelated to the scripts root.
- [ ] **Chipset-specific conversions only** — if converting multiple chipset variants (e.g., Mavros, Kaanapali, Clarence):
  - [ ] Used the most complete variant as the template (not the simplest)
  - [ ] All chipset-specific data (buildroots, binaries, LOADIMG groups) are present and match the `.cmm` source
  - [ ] No duplicate methods or functions (`grep "^def \|^class " | sort | uniq -d` returns nothing)
  - [ ] Line count is reasonable compared to the template (larger is OK if more buildroots/binaries)
  - [ ] Spot-checked a few values (addresses, binary names, search paths) against the original `.cmm`
- [ ] **Multi-file conversions only** — if the conversion spans multiple files or creates structural issues:
  - [ ] Each file follows the same structural pattern as its reference template
  - [ ] Dynamic imports (e.g., `importlib.import_module`) are properly wired between files
  - [ ] No manual method reorganization was attempted; used template-based approach instead
  - [ ] All files are syntactically valid and have no duplicate definitions
