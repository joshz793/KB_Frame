---
name: continue-doc
description: Keep a CONTINUE.md continuation document beside long-running or multi-session work, updated at every milestone, so a fresh session can resume it. Use when starting work that spans sessions or job runs, when resuming such work, or when handing it off.
---

# CONTINUE.md

A CONTINUE.md is the one place a fresh session reads to learn the state of a piece of work and what to do next. It sits beside the work, in the run or chain directory. Keep it current at every milestone while the work runs; it is a running record, not a write-up done at the end.

## Steps
1. **Create** `CONTINUE.md` from [TEMPLATE.md](TEMPLATE.md) in the work's directory before submitting or starting anything long. Fill in the purpose, layout, job plan and expense review.
   Done when: every template section has content or reads "none yet".
2. **Point** to it from the agent's persistent memory, or the project's index of active work, with a one-line hook that says to read it first.
3. **Update at every milestone.** Append a dated entry to "Status and findings" and fix any section the milestone has made stale. The milestones are:
   - **submission:** job IDs, dependencies, resources;
   - **job end:** state, return code, and the key numbers or the RESULTS.md section that holds them;
   - **finding:** what was learned and the path to the evidence;
   - **user decision:** the date, the decision and the reason given;
   - **end of a turn while work is still running:** the current state and when to check next.
4. **Close** when the work is finished: record the final status and where the results live, and move open follow-ups to "Judgment steps" or "Decided / deferred".
5. **Record the knowledge** in the project's cross-task lessons store, if the always-loaded project rules name one. Do this once, after closing, so it reflects the settled conclusions rather than first readings later revised. Record:
   - the final status and results of each model run;
   - the lessons that transfer beyond this task;
   - the user's decisions.
   Mid-work milestones go only in CONTINUE.md. If a finding contradicts something already in the store, record it as a conflict for later reconciliation; don't change the existing entry, if the project's rules say so.

Completion criterion for every update: a fresh session holding only this file can state the current status and the next action without opening a log.

## Rules
- Write absolute dates and times (2026-10-26 11:57).
- Numbers live in generated files such as RESULTS.md and results.csv, which CONTINUE.md cites. CONTINUE.md carries the judgment: interpretation, decisions and decision criteria.
- Record decided and deferred items, with their reason, so later sessions build on them instead of re-researching them.
- Status entries are append-only. Correct an earlier entry with a new dated entry.
- Write for a reader with no context: name files by path relative to the CONTINUE.md, and give exact status and resume commands.
