---
name: qreset-bring-core-out-of-reset
description: Port or verify a chipset's "qreset" debug-reset helper and its companion boot/control-core script that brings a secondary core out of reset (a target_utils/*.py pair reading addresses from chip_config.xml/.cfg) when scaffolded by copying a reference chipset's files. Use for requests like "port qreset to <new chipset>", "verify the addresses in chip_config.xml for <new chipset>", "why does this still have the old chipset's values", "regenerate this target_utils file for <new chipset>", or when a copy-pasted qreset/core-bringup file needs its hardcoded register addresses, imports, or chipset-name references checked before it's trusted on real hardware. Also use proactively whenever a new chipset directory under scripts/lldb_pkg/target/ looks like it was cloned from another chipset's directory and never fully adapted.
---

# qreset + bring-core-out-of-reset: verifying a ported debug helper before trusting it

A new chipset's directory under `scripts/lldb_pkg/target/<chip>/` is very
often scaffolded by copying a reference chipset's files wholesale, then
only partially adapted. The scaffolding is *supposed* to be pure copy +
targeted correction, but in practice several classes of leftover bug
survive silently because nothing fails loudly until the code runs against
real hardware. This skill is the checklist for finding and fixing them
before that happens.

**Never assume a copied address is correct just because the reference
chipset's own file works.** Every hardcoded register address must be
independently confirmed against the *new* chipset's own sources, or
explicitly flagged as unverified. This applies even when the reference
chipset's file is well-tested and "known working" — a correct-looking copy
that targets the wrong physical address on the new chipset is worse than
an obvious syntax error, because nothing will complain until a real board
gets a wrong write.

## Step 1 — Recognize scaffolding residue

Signs a file was copy-pasted from a reference chipset and never fully
adapted:

- A per-chipset Python module imports from the *reference* chipset's
  package instead of its own (e.g. `from lldb_pkg.target.<reference>.foo
  import bar` sitting inside `lldb_pkg/target/<new_chip>/...`). This is
  easy to miss because the import still resolves — Python doesn't care
  which package a name comes from, only that it exists — so nothing
  crashes, it just silently depends on the reference chipset's copy of the
  module staying compatible forever.
- A chipset name is hardcoded as a literal string inside an f-string,
  telnet/monitor command, or docstring example (e.g. `f"<REFERENCE_CHIP>.
  cpucp riscv dmi_write ..."`) in a file that otherwise builds its target
  name from a chip-agnostic variable. This is a strong signal the whole
  block predates a later refactor that made the surrounding code generic,
  and was never updated when the file was duplicated.
- A file's own header docstring (`@file <reference-file>.py`) or an
  internal docstring reference to a function name that doesn't exist
  anywhere in the current file — evidence of an even older name/structure
  that survived a rename.
- Numeric register addresses in the new chipset's config that are
  byte-identical to the reference chipset's, for a register that is
  *known* to live behind a per-chip remapped address elsewhere in the same
  config (e.g. other debug-bus base addresses for the same chipset already
  differ from the reference chipset's values, but this one particular
  address didn't move) — identical addresses aren't automatically wrong,
  but they're exactly the values that need the most scrutiny, precisely
  because "it's probably fine, it's the same as the working chipset" is
  the assumption that got them there in the first place.

## Step 2 — Classify each address before trusting it

Not all addresses in a per-chipset config carry the same risk of having
silently drifted between chipsets. Two broad classes, and the ground-truth
priority order for each:

- **Debug-fabric-internal / IP-block-internal addresses** (e.g. an
  internal cross-core debug-request register, a boot-ROM entry point, a
  fixed offset used only by an on-chip debug subsystem). These are often
  genuinely identical across an entire chip family, because the IP block
  and its internal addressing don't change even when the chip's external
  memory map does. A copied value here has a real chance of being correct
  as-is — but confirm it, don't assume it.
- **Remapped / SoC-memory-map addresses** (e.g. a debug-bus base address
  reachable through a per-chip Access Port, or an SoC-wide scratch/control
  register reached via the main system bus). These routinely move between
  chips in the same family, sometimes by a large, non-obvious offset. Treat
  any copied value in this class as guilty until proven innocent.

Ground-truth priority order when confirming a value for the new chipset,
highest confidence first:

1. **The new chipset's own already-adapted OpenOCD target-creation script**
   (the `.cfg`-equivalent file for that chipset), if it independently
   defines the same address. These files are typically hand-edited per
   chipset rather than blindly copied, so if it disagrees with the
   XML/data config, the `.cfg` file is usually the one that's correct and
   the XML/data config is the one that's stale.
2. **The new chipset's own low-level debug script(s)** for the exact same
   register/purpose (its own vendor debug-tooling source, not the
   reference chipset's copy of the same script).
3. **A shared, chipset-specific top-level orchestration script** that
   references the same address for a related purpose, if the narrower
   script doesn't mention it.
4. **Absence from all of the above** is itself a finding — say so
   explicitly rather than falling back to "no evidence against it, so
   leave it." It usually means the true source of that address lives in
   files this investigation doesn't have access to (e.g. firmware/hardware
   header sources versus debug-tooling scripts), and per-chip offsets in
   *those* files are exactly the kind of thing known to silently differ
   between chips in the same family even when a debug-tooling script
   reuses the same symbolic name for both.

If the reference chipset's and new chipset's low-level debug scripts for
the *same* register/purpose turn out to be byte-identical (or differ only
in a comment/chipset-name label), that is strong positive evidence the
address is a shared, family-wide constant and the copied value is correct
— but open both files and confirm this yourself; don't rely on a
recollection or summary from earlier in a conversation.

## Step 3 — Fix the mechanical bugs, not just the addresses

Once you've triaged which addresses need correcting, also fix (in the same
pass, since they're cheap and clearly-scoped):

- Cross-target imports → point at the new chipset's own package.
- Hardcoded reference-chipset name literals → use the chip-agnostic
  variable that's already in scope, matching the surrounding code's own
  pattern.
- Stale `@file` docstrings and stale function-name references in
  docstrings/comments → correct filename and actual current function name.

Do **not** use this pass as an excuse to refactor working logic, rename
things beyond fixing the residue, or "improve" code that isn't part of the
reported problem — the goal is a correct, minimal port, not a rewrite.

## Step 4 — Weigh corroborating evidence carefully

If you find a second file that appears to independently confirm a
disputed value (e.g. an older, unrefactored per-chipset script that
reaches the same register value via a different, more manual code path),
check whether that second file *also* still hardcodes the reference
chipset's name or otherwise shows the same scaffolding-residue signs from
Step 1. If it does, it's most likely the same copy-paste lineage
resurfacing in a second place, not independent proof — say so plainly
rather than citing it as corroboration. Weak evidence is still worth
mentioning, but label it as weak.

## Step 5 — Document verification status inline, at the point of use

For every value you touch, add a comment right next to it (in the
XML/config file, not just in your own notes) that says either:

- *Confirmed for `<new chipset>`*, citing the specific source file (and
  ideally the exact matching line) that proves it, or
- *Unverified for `<new chipset>`*, stating plainly that it was carried
  over unchanged from the reference chipset and that its true source
  wasn't available to check, so a reader doesn't mistake "nobody has
  flagged this" for "this was checked."

Future readers (including a future you) should never have to re-derive
whether a given value was actually checked.

## Step 6 — XML comment gotcha

When writing or editing an XML comment (`<!-- ... -->`), never let a
literal `--` (double hyphen) appear anywhere inside the comment body —
it's illegal per the XML spec and produces a parse error at a location
that can be far from the actual mistake (a later `-->` may be what
actually fails to parse, not the line with the stray `--`). Prose that
uses `--` as a dash, or a hex/offset expression that happens to produce
`--`, is an easy way to trip this. Use a semicolon, comma, or single
hyphen instead. **Always re-parse the file after any comment edit**, not
just after value edits — this class of mistake produces no diagnostic
until something actually tries to parse the file.

## Step 7 — If the file is missing entirely, regenerate it from the reference

If the file you're asked to port turns out to be absent from disk (not
present-but-wrong, just gone), that isn't a blocker — it usually means the
stale scaffolded copy was deliberately removed so a properly-verified
version could replace it. Reconstruct it from the reference chipset's file
plus the address-verification and scaffolding-residue work from Steps 1–5,
then diff the result against the reference file line-by-line (or have a
reviewer do so) to confirm only the intended, deliberate differences exist
— no accidental logic drift, no missing lines, no unintended extra
changes.

## Step 8 — Validate, then get it independently reviewed

Before considering the port done:

- Run a syntax check on any Python file changed (e.g. `py_compile`) and a
  well-formedness check on any XML file changed (e.g.
  `xml.etree.ElementTree.parse`). These are cheap, mandatory, and catch
  Step 6's mistake immediately — but they say nothing about whether an
  address is *correct*, only that the file parses.
- For a change this close to real hardware, don't rely solely on your own
  read of the source files. Have an independent pass re-derive each
  address claim directly from the raw source files (not from a summary of
  your findings), confirm no other file on the actually-executed code path
  needed a similar fix and was missed, and sanity-check second-order risk:
  fixing one wrong-but-inert address can turn a previously-unreachable
  wrong value *elsewhere* into a live, consequential one for the first
  time (e.g. a downstream step that was never actually reached before
  because an earlier step pointed at the wrong place). Call this out
  explicitly if you spot it — it's easy to miss when reviewing changes one
  at a time instead of end-to-end.
- State plainly, in whatever you hand back, which values are
  hardware-confirmed versus plausible-but-unverified. Don't let a clean
  syntax/XML check read as "this was fully verified."

## Output checklist

- [ ] Every address touched has a documented confidence level and, for
  confirmed ones, a cited source file/line.
- [ ] Cross-target imports point at the chipset's own package.
- [ ] No hardcoded reference-chipset name literals remain in code that
  otherwise builds names generically.
- [ ] File headers and internal docstring references match the actual
  current filename/function names.
- [ ] No `--` inside any XML comment added or edited.
- [ ] Syntax check (Python) and well-formedness check (XML) both pass.
- [ ] If the low-level source files for the reference and new chipset were
  compared, note whether they were identical, near-identical, or
  meaningfully different — this changes how much you can trust a copied
  value.
- [ ] Values left unverified are labeled as such in-place, not silently
  carried over.
- [ ] An independent review pass re-checked the address claims and scanned
  for any other file on the same code path that still needed a fix.
