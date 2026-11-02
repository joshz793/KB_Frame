---
name: slurm-chain
description: Set up multi-hour Slurm calculations as a smoke-gated, resumable job chain with self-checks, a jobs log and automatic result collection. Use when planning or submitting long HPC jobs, building a job chain, or resubmitting a failed or timed-out job.
---

# Smoke-gated Slurm chain

Long calculations run unattended as a chain:
- a cheap **smoke** job proves every code path and measures cost;
- **production** jobs run only if it passes;
- **post-processing** collects whatever finished into a results file.

The chain's state lives in a CONTINUE.md (skill `continue-doc`), created before the first submission. Site specifics (queues, limits, the maximum run length, scratch, environment) come from the project's site notes: a `SLURM_SITE.md` beside this file, or the file the project's agent instructions name.

## Steps
1. **Expense review.** For each production run, take the closest measured analogue (`sacct -j <id> -o Elapsed,MaxRSS,AllocTRES`) and scale it by the step's size driver: for example basis-functions³ for integral-bound steps, or the CI dimension for CI-bound ones. Record size, memory, time, the basis of the estimate and the request in CONTINUE.md. Request at least 2× the memory estimate and 3× the time estimate, within the queue limits.
   Submit only runs estimated to finish within the project's maximum run length (stated in its site notes). A run estimated above that is redesigned (smaller cluster, cheaper method, split into steps) or proposed to the user, not submitted.
   Done when: every run has a memory and time estimate tied to a measured analogue or a stated formula.
2. **Smoke gate.** A short debug-queue job runs every code path at reduced size (one root, one variant, fewer steps), runs the self-checks, and records measured time and memory. Its gate conditions are explicit, and it exits non-zero when any of them fails.
   - **Cost uncertain:** submit the smoke job alone, read its measured numbers, revise the requests, then submit production.
   - **Cost known:** submit production with `--dependency=afterok:<smoke id>` and `--kill-on-invalid-dep=yes`.
3. **Resumable production.** Each job checkpoints and skips finished work on a rerun (a state JSON of finished steps, existing outputs, restart files). Resubmitting the same job is the resume path.
4. **Self-checks.** Each job asserts its invariants (convergence, reproduction of a reference value, expected output counts) and exits non-zero on a failure. The job script confirms the expected outputs exist before it reports success.
5. **Post-processing.** Analysis and collect jobs depend `afterany` on production. The collect script writes `RESULTS.md`, with a "Missing" list naming every absent output, so partial results still land.
6. **Log.** A `note()` shell helper appends date, job ID, job name and message to `jobs.log` at job start, at each return code and at each check.
7. **Submitter.** `submit_all.sh smoke|prod|full [--only <job>]` submits the chain and logs every job ID.
8. **Wait.** Watch with one background loop on `sacct` state that exits on every terminal state (COMPLETED, FAILED, CANCELLED, TIMEOUT, OUT_OF_MEMORY, NODE_FAIL). Update CONTINUE.md at each completion.

Completion criterion: every job in the chain has a recorded ID, a self-check and a resume command in CONTINUE.md, and the collect step names any missing output.
