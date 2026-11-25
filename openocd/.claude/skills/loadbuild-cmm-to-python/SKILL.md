---
name: loadbuild-cmm-to-python
description: Port a Qualcomm Trace32 std_loadbuild_la.cmm script (target-specific, one per chipset) to a Python std_loadbuild_la_<target>.py LLDB command, using the existing Clarence/Kaanapali conversion in scripts/lldb_pkg/target/ as the worked template — ask the user for the .cmm behind each before trusting it, since no .cmm/.py reference is bundled with this skill. Use when asked to convert, port, or add loadbuild support for a new chipset/target (e.g. "convert Molokai's loadbuild to Python", "add std_loadbuild support for skyros").
---

# loadbuild .cmm → Python conversion

Ports a target's `std_loadbuild_la.cmm` (Trace32 PRACTICE script) into a
`std_loadbuild_la_<target>.py` LLDB command that drives `jtagprogrammer.py`
instead of `JtagProgrammer.cmm`. The `.cmm` is always the **source of truth**
for the conversion — match its logic exactly, including every fallback
branch and conditional requirement, even if the sample `contents.xml` you're
given doesn't currently exercise that branch.

**Naming convention — target-specific, not meta-specific.** Every chipset's
own copy of this script is conventionally just called `std_loadbuild_la.cmm`
(it lives inside that target's own meta/build tree, so there's no collision).
Once ported into a project, disambiguate by target: `std_loadbuild_la_<target>.py`
/ `std_loadbuild_la_<target>.cmm`, e.g. `std_loadbuild_la_kaanapali.py`. This
project's own already-converted scripts follow this convention loosely — see
`scripts/lldb_pkg/target/clarence/Storage/std_loadbuild_la.py` and
`scripts/lldb_pkg/target/kaanapali/Storage/std_loadbuild_la.py` (or wherever the
current project keeps its converted scripts; check fresh each session, since
more targets get added over time). Note that only the *filenames* follow the
convention — the internal function/class names inside them still use the
short historical suffix (`resolve_kaana`,
`_kaana_binary_list`, `KaanaBuildVars`, `loadbuild_kaana`, `std_loadbuild_kaana`,
etc. — no `_la_kaanapali` in the identifiers themselves). Follow that same
internal-naming pattern for a new target: file named
`std_loadbuild_la_<target>.py`, but internal identifiers suffixed with a
short target tag (`resolve_<tag>`, `_<tag>_binary_list`, `<Tag>BuildVars`,
...), not the full `_la_<target>` filename stem.

The script is owned by the **target/chipset**, not by any one meta build. A
given `contents.xml` you're handed is just one sample drop used to write and
test the resolver against — the same `std_loadbuild_la_<target>.py` must keep
working against *future* meta drops of that same chipset (different
`build_id`s, different `TZ_BUILDID`, etc.), as long as the chipset's
partition layout/binary set doesn't change. Don't hardcode values that are
specific to the one sample build you were given (a `build_id` string, a
resolved `TZ_BUILDID`, a specific network path) — only hardcode what the
`.cmm` itself hardcodes (image names, `cmm_file_var`/`cmm_root_path_var`
names, partition/loadimg group logic).

## Canonical worked example (ask the user for it — nothing is bundled)

This skill does **not** bundle any `.cmm` or converted `.py` reference files.
Trace32 `.cmm` sources cannot be upstreamed into this repo, and previously
converted Python is likewise not distributed as skill reference material —
both live only in the current project (and only for the targets already
ported there) or on the user's machine. Every conversion session has to
re-establish the worked example from scratch, from whatever the *current
project* already has:

1. **Find what's already converted in this project.** Look for existing
   `std_loadbuild_la*.py`/`std_loadbuild_<tag>.py`-style scripts — in this
   project's case that's `scripts/lldb_pkg/target/<target>/Storage/std_loadbuild_la.py`
   (e.g. the `Clarence` and `Kaanapali` subdirectories), but re-check the
   actual layout each session since new targets get added over time and a
   different project may organize this differently. These are the only
   worked examples available — there is no separate bundled copy to fall
   back on.
2. **Ask the user for the `.cmm` behind each already-converted script you
   intend to use as a template.** The `.py` alone is not sufficient — you
   cannot verify it against its source without the `.cmm`, and a `.cmm` may
   have been hand-edited or drifted after the original conversion. Ask the
   user to attach the original `std_loadbuild_la.cmm` (or `loadbuild_<tag>.cmm`)
   for whichever existing target(s) are the closest match to the new target,
   via Claude Code's `@` file-reference syntax, e.g.
   `@path/to/std_loadbuild_la.cmm`. Read the `.cmm` side-by-side with the
   already-converted `.py` to confirm every `&XXX_BUILDROOT`/`&XXX_BINARY`
   variable, every `FILEXIST FATALEXITUSER` check, and every fallback chain
   in the `.cmm` has a real counterpart in the `.py` before trusting that
   `.py` as a structural template for the new target. If something in the
   `.py` doesn't match the `.cmm` you were given, flag the mismatch to the
   user rather than silently propagating it into the new conversion.
3. **Check what JTAG/LLDB driver the already-converted scripts call into**
   (in this project, each `Storage/<Target>/` directory has its own
   `jtagprogrammer.py` that `jtp._dispatch(...)` calls go through) — use
   the copy that actually lives alongside the target you're extending,
   don't assume a single shared file across the whole project.
4. **The new target's own `std_loadbuild_la.cmm` and its `contents.xml`** —
   the two genuinely new inputs per conversion; `contents.xml` is where every
   `cmm_root_path_var` / `cmm_file_var` / `short_build_path cmm_var` mapping
   comes from. **Require the user to attach both directly using Claude
   Code's `@` file-reference syntax** (e.g. `@path/to/std_loadbuild_la.cmm`,
   `@path/to/contents.xml`) rather than pasting inline — a pasted excerpt can
   be silently truncated or reformatted, and this conversion depends on
   every line of the `.cmm` being exact. Don't start the conversion procedure
   until both are provided this way.

## How meta-path / contents.xml resolution actually works (context, not something you drive)

`std_loadbuild_la_<target>.py` never reads `contents.xml` cold — it reads it
through a shared debug-context object (`g_dbgContext` in this project's
`scripts/utils/debug_context.py`), which is only populated by prior LLDB
commands run in the *same debug session*. This is the pattern the project's
already-converted scripts implement; **a different project may have its own
equivalently-named infrastructure with different file/class names — check
the current project's actual files before assuming these exact names apply.**
The chain, as implemented in this project:

1. `meta_dir -s <metabuild_path>` (an LLDB command, handler class
   `metaDirectory` in `scripts/openocd_lldb.py`) is run first.
   It calls an `initDebug()`-equivalent function, which sets
   `g_dbgContext.contentsXmlPath = "<metabuild_path>/contents.xml"` and asserts
   that file exists — this is the **only** place that path gets set.
2. That init function also constructs an `openocdConfig` object, which
   internally builds a `metaInfo` object (`scripts/utils/meta_info.py`). Its
   `__getMetaInfo()`-equivalent parses the *same* `contents.xml` a second time
   to populate its own per-image `ImageInfo`/`binaryInfo` objects — this is
   the object your `resolve_<target>()` receives as its `meta: metaInfo`
   argument and calls `meta.getMetaPath()` / `meta.getImageBasePath(<image>)`
   on.
3. `resolve_<target>()` (your converted code) then does its **own**, separate
   `ET.parse(str(g_dbgContext.contentsXmlPath))` — it does not reuse
   `metaInfo`'s parsed tree. This is intentional/existing duplication (see
   `resolve_from_contents_xml`/`resolve_from_meta` in
   `scripts/lldb_pkg/target/clarence/Storage/std_loadbuild_la.py` and
   `resolve_kaana` in `scripts/lldb_pkg/target/kaanapali/Storage/std_loadbuild_la.py`,
   once the user has attached the matching `.cmm` files so you can confirm
   this pattern against source) — don't try to "fix" it by sharing state;
   it's how the base pattern already works, and changing it is out of scope
   for a loadbuild port.
4. `image_path -i <imagename> -s <path>` (handler class `imagePath` in
   `scripts/openocd_lldb.py`) is optional **for the user to
   invoke**, but the override mechanism it feeds is **mandatory in every
   converted script** — this is not a special case, it is the main reason
   this tooling exists. Engineers constantly point one or two images (`boot`,
   `tz`, `aop`, ...) at a local engineering build instead of the shared meta
   drop, via a `.vscode/settings.json` config value in this project
   (`BOOT_BUILD_PATH`/`TZ_BUILD_PATH`/`AOSS_BUILD_PATH`, wired into
   `image_path` calls in `.vscode/launch.json`'s launch sequence — check
   how a different project actually wires this before assuming identical
   variable names). When set, `image_path` calls
   `metaInfo.setImageBasePath(imageName, imagePath)`, which makes
   `meta.getImageBasePath(image_name)` return that override instead of the
   meta-derived path.

   **You do not need to know or reason about which images are overridden while
   writing the resolver** — that decision is made later, per-run, from the
   project's own config, not from anything in the `.cmm` or `contents.xml`.
   Your job is simply to include, in `resolve_<target>()`, the same
   override-rebasing loop the already-converted scripts have (`_image_buildroot_map`-
   style logic in `std_loadbuild_la.py`, `_kaana_buildroot_map` in the Kaanapali
   variant): for every image name the target's
   buildroots correspond to (`boot`, `tz`, `aop`, `tme`/`ssg`, `acp`, `pdp`,
   `soccp`, `dcp`, `cpucp`, etc. — whatever buildroots this target has), call
   `meta.getImageBasePath(image_name)`; if it
   differs from the buildroot path parsed straight out of `contents.xml`,
   rebase that buildroot and every binary field under it to the override path.
   Write this loop unconditionally for every buildroot the target has — don't
   skip it for buildroots that seem unlikely to be overridden; you have no way
   to know that in advance, and the whole point is that any of them might be
   at override time.
5. Only after both `meta_dir` (mandatory) and any `image_path` calls (optional)
   have run does `std_loadbuild_la_<target> VERIFY` / `LOADCOMMON` / etc. have
   anything valid to resolve — calling a `std_loadbuild_*` command before
   `meta_dir` raises inside `resolve_<target>()` because
   `g_dbgContext.contentsXmlPath` is still `None`.

`set_chipset -s <chipset_name>` (handler class `chipset` in
`scripts/openocd_lldb.py`) is independent of this chain — it sets
`g_dbgContext.chipsetName`, used by the JTAG driver to pick the right OpenOCD
target name (`<CHIPNAME>.apss.core0`) and to cross-check the meta's declared
`<chipset>` against the project's per-chipset config directory. Both
`meta_dir` and `set_chipset` are needed; order between the two doesn't matter
for loadbuild purposes, but this project's launch configs run `set_chipset`
first.

## Human setup steps this skill cannot do for the user

None of this can be driven by an agent reliably — it depends on the user's
physical bench, network share access, and IDE state, and it depends on
*this specific project's* file layout. **Tell the user to do these
themselves**, in this order, before any conversion work is tested live:

1. **Confirm the network path to the new target's meta build is reachable**
   from the machine running LLDB (e.g. `ls //server/share/<Target>.LA.x.x-...`
   in a shell). This is commonly a UNC/network-share path (Windows) —
   VPN/mapped-drive issues here look identical to "meta doesn't resolve" bugs
   in the script, and waste time if misdiagnosed as a code problem.
2. **Get the `.cmm` behind whichever already-converted script(s) you plan to
   use as a template, plus the new target's own `std_loadbuild_la.cmm` and
   its `contents.xml`**, and attach all of them directly to Claude Code using
   `@` file references (e.g. `@path/to/std_loadbuild_la.cmm`,
   `@path/to/contents.xml`) rather than pasting inline. None of the `.cmm`
   sources or previously converted `.py` files are bundled with this skill —
   they cannot be upstreamed into this repo — so every session has to source
   them fresh: the template `.cmm`(s) from wherever the user keeps them, and
   the new target's `.cmm`/`contents.xml` as the two inputs genuinely specific
   to this conversion. A pasted excerpt risks silent truncation/reformatting
   that a direct file reference avoids.
3. **Confirm `jtagprogrammer` itself already runs successfully against this
   target before testing the new loadbuild script.** The loadbuild script is
   a thin flow on top of `jtagprogrammer.py`'s `jtp._dispatch(...)` — if
   `jtagprogrammer` can't yet talk to the target (JTAG bring-up broken, elf
   path wrong, etc.), the new script will fail for reasons that have nothing
   to do with the conversion itself, and that's a confusing thing to debug
   together. Get a working `jtagprogrammer` command run on this target first,
   in isolation, before layering `std_loadbuild_la_<target>` on top.
4. **Find this project's own LLDB-launch config** (`.vscode/launch.json`) —
   specifically the same launch entry that already imports/runs
   `jtagprogrammer` — and **add a new `command script import` line for the
   new `std_loadbuild_la_<target>.py`** alongside it, in the same launch
   sequence. A script that compiles fine is still invisible to LLDB until an
   import line for it exists somewhere in the launch sequence — this is the
   single most common "why doesn't my new command exist" gap, regardless of
   what the launch config happens to be named in this project.
5. **Add/update this project's own equivalent of `CHIPSET`/`META_PATH` config
   values** (`.vscode/settings.json`) so they point at the new target's
   chipset name and meta network path. Leave any `JTAG_ELF_PATH`/
   `JTAG_DTB_PATH`-style overrides empty unless they specifically need one —
   those two auto-resolve from `contents.xml` when blank; confirm whether
   this project's driver does the same before assuming it.
6. **Confirm this project already has a working OpenOCD/JTAG target config
   for the new chipset** (per this skill's scope: the loadbuild conversion
   assumes JTAG/`jtagprogrammer`-equivalent bring-up for the target already
   works — verifying *that* is a separate, out-of-scope concern from this
   skill). If it doesn't exist yet, that's a prerequisite blocker, not part
   of this conversion.
7. **Launch the actual LLDB/JTAG debug session from the IDE** (not just run
   the new `.py` file standalone) — import order in the launch sequence
   matters (the core driver script first, then `jtagprogrammer`, then every
   loadbuild script including the new one, then `set_chipset`, then
   `meta_dir`, then any `image_path`-equivalent overrides). Ask the user to
   do this step themselves rather than trying to simulate or script around
   it.
8. **STOP POINT — before touching LOADCOMMON or any other flash-affecting
   command, hand control back to the user and ask them to run
   `std_loadbuild_la_<target> VERIFY` themselves** in their own LLDB session
   and paste back the full output. Do not treat this as optional or as
   something you can shortcut by re-reading the code — `VERIFY` exercises the
   real `meta_dir`/`contents.xml`/engineering-override resolution chain on the
   user's actual bench/network state, which is not observable from source.
   Wait for the user's pasted output before proceeding. For every field
   `verify_<target>_vars` requires, confirm it printed a real path, not
   `None`, and cross-check a few of those paths actually exist on disk.
   Expect the output to arrive in batches if it's long (a chat UI will often
   visibly truncate a large paste at the same cutoff point twice; that's a
   UI/paste artifact, not the command actually stopping — ask for the next
   chunk rather than assuming the print statement itself failed). Only once
   the user confirms every required field resolved to a real, existing path
   should the conversion proceed toward `LOADCOMMON`.
9. **Before any real `LOADCOMMON` on hardware, confirm the device's physical
   state matches what the command expects** (EDL mode / PBL halted / core
   accessible via JTAG) — this is a bench/hardware step, not something
   inferable from source code.

## File map

Nothing is bundled with this skill — no `.cmm`, no previously converted
`.py`. Everything below has to be located in, or requested from, the current
project/user each session:

| Concept | What it does | Where this project puts it |
|---|---|---|
| Already-converted target scripts | The only available worked examples; ask the user for the `.cmm` behind each before trusting it as a template | `scripts/lldb_pkg/target/<target>/Storage/std_loadbuild_la.py` (e.g. `Clarence`, `Kaanapali` — re-check the actual layout each session, new targets get added over time) |
| JTAG/LLDB driver | `loadbuild_<target>()` calls into it via `jtp._dispatch(...)` | `scripts/lldb_pkg/target/<target>/Storage/jtagprogrammer.py` — per-target copy, don't assume one shared file |
| Debug-context singleton | Holds `contentsXmlPath`, `chipsetName`, etc.; set by `meta_dir`/`set_chipset`, read by the resolver | `scripts/utils/debug_context.py` (`g_dbgContext`) |
| Meta/image info object | Parses `contents.xml` for image paths + engineering overrides; passed into your resolver as `meta: metaInfo` | `scripts/utils/meta_info.py` (`metaInfo`/`ImageInfo`/`binaryInfo`) |
| Meta-dir init | Runs once per `meta_dir -s ...`; sets the debug-context's contents.xml path, builds the meta/image-info object | `scripts/utils/initDebug.py` |
| LLDB command definitions | Defines `meta_dir`, `set_chipset`, `image_path` commands that must run before any loadbuild command | `scripts/openocd_lldb.py` |
| Launch/debug-session config | The actual sequence of `command script import` + command calls a user runs; **must be updated** with an import line per new target script | `.vscode/launch.json`'s `"JtagProgrammer"` config |
| Per-launch variables | `CHIPSET`, `META_PATH`, and any per-image build-path overrides substituted into the launch config | `.vscode/settings.json` |
| Per-chipset OpenOCD target config | Reset config, CTI/AP numbers, `qreset`-equivalent proc — a prerequisite this skill assumes already exists and works | `config/target/<chipset>/<chipset>.cfg` |

A different project may organize these differently — treat the paths above
as this project's current layout, not a fixed convention, and re-verify with
Glob/Grep before relying on them.

Also needed, not bundled: **the `.cmm` behind whatever template script(s) you
use, plus the new target's `std_loadbuild_la.cmm` and its `contents.xml`** —
ask the user for all of these up front, via `@` file references.

## Two-phase architecture (mirror this exactly)

Every `std_loadbuild_la_<target>.py` has the same shape as the existing
`scripts/lldb_pkg/target/kaanapali/Storage/std_loadbuild_la.py` (once the user
has attached its source `.cmm` so the pattern can be confirmed against it):

- **Phase 1 — variable resolution** (`resolve_<target>` + `verify_<target>_vars`):
  parse `contents.xml` into a dataclass, apply engineering-path overrides, then
  assert every file required for the requested `loadoption` actually exists on
  disk. This never touches the live target — it's pure file-system/XML work.
- **Phase 2 — load flow** (`loadbuild_<target>`): drives `jtagprogrammer.py`'s
  `jtp._dispatch(...)` for PROVISION / ERASE / LOAD (GPT, binaries) / PATCH /
  set-active-boot / LOADIMG / LOADFULL-fastboot, in the *exact* order and
  conditional structure the `.cmm`'s `MAIN` subroutine uses.

## Conversion procedure

1. **Read the whole `.cmm`** (`std_loadbuild_la_<target>.cmm`). Identify:
   - Every `&XXX_BUILDROOT` / `&XXX_BINARY` variable the `MAIN` subroutine
     references — these become dataclass fields.
   - The `FILEXIST FATALEXITUSER` block inside `LOADCOMMON`/`LOADFULL` — this
     becomes `verify_<target>_vars`. Copy every check across; note any that
     are wrapped in an `IF` (conditional requirements — see pitfall below).
   - The `&binary_list=...` construction block — becomes `_<target>_binary_list`.
     Preserve fallback chains (`IF (FILE.EXIST(...)) ... ELSE IF (FILE.EXIST(...))`)
     — CMM's `ELSE IF` means "try binary A, if missing try binary B", and BOTH
     paths must exist as dataclass fields even if only one is populated by the
     current `contents.xml`.
   - The `&SearchPaths=...` construction — becomes `_<target>_search_paths`.
   - The `LOADIMG` branch's per-group `IF ("&loadimages"=="xxx")` blocks —
     become `_<target>_loadimg_list`, one `elif` per group.
   - Any target-specific detection logic (e.g. Kaana's `TZ_BUILDID=="KAANAPALIDEV"`
     check controlling whether `HYP_DEVCFG_BINARY` is required).

2. **Decide: extend `BuildVars`, or copy the pattern fresh?** If the new target
   shares most buildroots/binaries with an existing converted target (e.g. another
   Kaanapali-family chipset), subclass that target's `*BuildVars` dataclass the
   way `KaanaBuildVars(BuildVars)` extends `BuildVars` — add only the new/changed
   fields, and reuse `_la_resolve`/`_jtagprog_elf_path`/`_get_jtp` from the base
   module rather than re-implementing them (see the
   `from utils.std_loadbuild_la import (...)` block near the top of
   `scripts/lldb_pkg/target/kaanapali/Storage/std_loadbuild_la.py` — note this
   project's *module* is literally named `std_loadbuild_la`, matching its
   Python import path; only the on-disk *filename* under `Storage/<Target>/`
   is what disambiguates targets, so don't assume a different project's
   import path will match its filename convention verbatim). If it's
   structurally very different, start a fresh dataclass but keep the same
   field-naming convention (`&XXX_BUILDROOT` → `XXX_BUILDROOT`, `&XXX_BINARY`
   → `XXX_BINARY`).

3. **Write the dataclass.** One field per CMM variable, `Optional[str] = None`.
   Mark optional-on-target fields with a trailing `# optional` comment. **Every
   field the `.cmm` ever references — including ones only used inside an `ELSE`
   branch — must exist as a real dataclass attribute**, even if it stays `None`
   for the current `contents.xml`. A plain `@dataclass` raises `AttributeError`
   on an undeclared attribute access, not `None` — this is the single most
   common bug in this conversion (see Pitfalls below).

4. **Write the resolver.** Parse `<builds_flat><build>` entries: `windows_root_path`
   / `linux_root_path` with `cmm_root_path_var` → buildroot fields; children with
   `cmm_file_var` → binary fields (`file_name`+`file_path` joined, `"."` filename
   means directory-only). Also handle `<short_build_path cmm_var="...">` for
   build-ID strings (e.g. `TZ_BUILDID`), and `<product_info>` tags (`chipid`,
   `meta_type`, `default flavor=`).
   **Always run every parsed text value through the same `_strip_tokens()`/
   `_clean()` helpers** — never a bare `.strip()`. `${var:default}` template
   tokens appear in `short_build_path` text too, not just `file_path`/`file_name`
   (this was a real bug: `TZ_BUILDID` was read with bare `.strip()` and ended up
   holding the literal string `"${tz_bid:XKWAANAT}"` instead of `"XKWAANAT"`).

5. **Write `verify_<target>_vars`.** One `_require(bv.FIELD, "FIELD")` per
   non-optional `FILEXIST FATALEXITUSER` line in the `.cmm`. For CMM's
   `IF (cond) ( FILEXIST ... )` conditional requirements, wrap the `_require`
   in the equivalent Python `if`. Don't skip these just because they don't fire
   for the target's current build — a future build variant may hit them.

6. **Write `_<target>_binary_list` and `_<target>_search_paths`.** Mirror the
   CMM's append order (cosmetic, but makes diffing easier) and every
   `IF/ELSE IF (FILE.EXIST(...))` fallback exactly — each fallback pair needs
   both dataclass fields populated from `contents.xml`/file_vars, and the Python
   `if / elif` must check both, in the same order as the CMM.

7. **Write `_<target>_loadimg_list`.** One `elif loadimages == "...":` per CMM
   `IF ("&loadimages"=="...")` block under `LOADIMG`. Return
   `(",".join(names), ",".join(dirs))` same as the existing examples.

8. **Write `loadbuild_<target>`.** Structural copy of
   `scripts/lldb_pkg/target/kaanapali/Storage/std_loadbuild_la.py`'s
   `loadbuild_kaana` — resolve →
   verify → PROVISION → ERASE → (LOADCOMMON/LOADFULL: GPT loop → binaries
   loop → patch loop → activeboot) → LOADIMG → LOADFULL-fastboot. Reuse
   `_get_jtp()`/`_jtagprog_elf_path()` from the base module (see
   `scripts/lldb_pkg/target/clarence/Storage/std_loadbuild_la.py`) unless the
   target's ELF/DTB layout genuinely differs.

9. **Write the LLDB command boilerplate.** Copy
   `scripts/lldb_pkg/target/kaanapali/Storage/std_loadbuild_la.py`'s
   `std_loadbuild_kaana`
   command-dispatch function, `VERIFY`/`FASTBOOT` subcommands, the
   `std_loadbuild_la_<target>Command` adapter class, and `__lldb_init_module`
   verbatim in structure — only the function/command names change.

## Verification workflow (do this before ever running LOADCOMMON on hardware)

1. `python -m py_compile <path/to/new/std_loadbuild_la_<target>.py>` — syntax check.
2. **Do not run `VERIFY` yourself, and do not treat a code read-through as a
   substitute for it.** Ask the user to run `std_loadbuild_la_<target> VERIFY`
   themselves in their own LLDB session — this is the stop point from the
   "Human setup steps" section above — and to paste back the **full** printed
   field dump (it's long — expect it in batches). For every field
   `verify_<target>_vars` requires, confirm it printed a real path, not `None`.
3. Cross-check each printed path actually exists on disk (`ls`/`stat` over the
   resolved UNC/network path) — VERIFY only resolves paths, it does not call
   `verify_<target>_vars`, so a `None` or a nonexistent path won't surface as
   an error until LOADCOMMON actually runs.
4. Only after the user has confirmed every required field resolves AND exists
   on disk, greenlight `LOADCOMMON`.

## Pitfalls learned from the Kaanapali conversion (check for these explicitly)

- **Undeclared dataclass field referenced in code** → `AttributeError` at
  runtime, not `None`. Happened with `KERNEL_ABL_BINARY`: referenced in 4
  places but never declared on `KaanaBuildVars`, so it crashed unconditionally
  inside `_kaana_search_paths` (the crashing line accessed the attribute as
  part of a ternary *condition*, so no `if hasattr(...)` guard protected it).
  **Fix**: grep the finished script for every `bv.<NAME>` access and confirm
  each `<NAME>` is declared in the dataclass.
- **`short_build_path` text read with bare `.strip()`** instead of running
  through `_strip_tokens()` — leaves `${var:default}` template syntax in the
  resolved value instead of resolving to the default. Broke a `TZ_BUILDID`
  equality check (`if bv.TZ_BUILDID == "KAANAPALIDEV":`) silently — the
  comparison just always failed instead of erroring, so the bug hid until
  the resolved value was inspected via `VERIFY`.
- **CMM `ELSE IF (FILE.EXIST(...))` fallback chains dropped in conversion**.
  It's easy to convert only the primary `IF` branch and forget the `ELSE IF`
  fallback field entirely (missing dataclass field, missing `contents.xml`
  parse, missing `elif` in the binary-list builder) — found and fixed for
  `TZ_QTI_CONFIG_BINARY` and `TZ_DEVCFG_BINARY` fallbacks in
  `_kaana_binary_list` / `_kaana_loadimg_list`.
- **Conditional required-field checks tied to a build-variant string** (e.g.
  `HYP_DEVCFG_BINARY` only required `IF ("&TZ_BUILDID"=="KAANAPALIDEV")`) are
  easy to drop entirely from `verify_<target>_vars` since they don't fire on
  every build. Convert them anyway, gated on the same condition.
- **A batch `LOAD` operation silently skips a file it can't locate** —
  `_raw_program`'s operation-3 path (`jtagprogrammer.py`) prints
  `File not found: ...` and `continue`s past it; it does **not** fail the
  overall `LOAD`/`LOADCOMMON`. A clean `SUCCESS` from `loadbuild_<target>`
  does not guarantee every file actually landed on the device — spot-check
  critical sectors (first bytes of each boot-critical image) via
  `jtagprogrammer 1 sector=<N>` against the source file's bytes at the
  corresponding file offset before trusting a flash.
- **Running the wrong/stale target script produces confusing, inconsistent
  symptoms** that look exactly like flash corruption (partial writes, zeroed
  sectors) but are actually a user/tooling error, not a code bug. If a
  readback looks wrong, first confirm the *correct* `std_loadbuild_la_<target>`
  module was actually the one invoked.

## When you don't have all the target's binaries in `contents.xml` yet

It's fine — and expected — for `verify_<target>_vars` to declare requirements
that the *current* `contents.xml` doesn't satisfy (e.g. a buildroot/build not
present in this particular meta drop). Convert the `.cmm`'s requirement anyway;
`verify_<target>_vars` will raise a clear `FileNotFoundError` naming the missing
field the first time someone runs `LOADCOMMON` against a build that's actually
missing it. Don't weaken or drop a requirement just because one sample
`contents.xml` doesn't exercise it.
