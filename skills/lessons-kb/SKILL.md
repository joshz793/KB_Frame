---
name: lessons-kb
description: Read and write a project's lessons-learned knowledge base (lessons-kb framework, `kbcli`). Use before planning a calculation or experiment on a system the project has worked on or one like it, when a piece of work closes and its findings should be recorded, and whenever a finding contradicts something the KB already says.
---

# Lessons-learned KB

The project keeps what it learned (systems, models tried, lessons, user decisions) in a KB that `kbcli` reads and writes. The rules are in the framework's `docs/GUIDE.md`; this page is the short version. If the project's own instructions name where the KB and the framework are, use those. Otherwise `kbcli` finds `lessons-kb.json` by searching upward from the working directory.

Run `kbcli` from the project directory that holds `lessons-kb.json` (`python kbcli.py` from the framework checkout if it is not installed). Any python with numpy works. Add `--json` for machine-readable output.

## Before planning
1. `kbcli profiles` to see which class of systems the project supports. A profile's own README says how to describe a system of that class.
2. `kbcli similar --profile <name> -i <field>=<value> ... [--xyz geom.xyz] [--software <codes>] [--platform <site>]` finds the known neighbours; `kbcli lessons --system <id>` lists every lesson that applies to a known system. `kbcli condense --system <id> --out focused.json` writes a small copy to read.
3. Read the applicable lessons with `kbcli show <id>`.
4. Say which `established` lessons you follow and which you set aside, and why. A lesson marked `[contested: C-...]` has an open conflict: read `kbcli conflict show C-...` and say which side you follow.

## When a piece of work closes
Only after its results are interpreted and any second thoughts are settled. Interim results belong in your working notes, not the KB.
1. Record with `kbcli add-system`, `add-model`, `update-system` or `add-lesson` (drafts as JSON files: GUIDE §3 and §5). Give evidence paths, a `context` (systems, observables, methods, conditions), the broadest scope that is true, and an honest confidence.
2. A decision the user gave for one situation (for example "stop this run") goes only in the system's `user_decisions`. It becomes a lesson only if it is meant to carry over; ask when that is unclear.
3. Before adding a lesson run `kbcli related -f draft.json`. If it contradicts, corrects or repeats an existing lesson, do not add it as a lesson. Record it as a conflict:
   `kbcli conflict add -f draft.json --contradicts <lid>... --why "..."`
4. Check where each new lesson landed (`add-lesson` prints it) and copy its evidence with `kbcli supplement <lid> --referenced`.
5. Run `kbcli validate`, then `kbcli render`.

## Rules that do not bend
- In a normal session the KB only grows. Never change what an existing lesson says or where it applies (no editing its text, scope or context, no superseding, moving or re-scoping), and never overwrite a filled field. Changes like that happen only in a reconciliation session, run by the `kb-reconciler` agent when the user asks for one (GUIDE §12). A lesson's metadata (evidence annotations, `coverage`, `verification`, `reviewed`) may be edited in any session.
- Never hand-edit the KB files. `kbcli` locks, validates, routes between the shared and project KBs, and backs up, and other sessions may write at the same time.
- If no profile fits, use `generic`, or add a profile (GUIDE §8).
