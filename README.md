# lessons-kb

A lessons-learned knowledge base for computational work. People and AI agents (e.g. Claude Code) use it to record:
- the systems they studied;
- the models they tried on each;
- the transferable lessons they learned.

They read it before planning the next calculation. Records are content-hashed, so knowledge bases can be split, merged and shared. A **profile** per class of systems defines identity, a geometry-based fingerprint and similarity, so lessons reach related systems. The `generic` profile, for any molecule or cluster, is built in.

Normal sessions only add to the KB. A finding that contradicts an existing lesson is recorded as a **conflict**. Existing lessons change only in dedicated **reconciliation** sessions (`agents/kb-reconciler.md`).

The rules for readers, writers and profile authors are in [docs/GUIDE.md](docs/GUIDE.md).

## Install
Python ≥ 3.6 with numpy:
```bash
pip install git+<this repository>@<tag>     # provides the `kbcli` command
# or, without installing: python <checkout>/kbcli.py ...
```

## Start a KB in a project
```bash
cd my-project
kbcli init                         # writes lessons-kb.json and an empty shared KB db/
kbcli --db db_project init         # optional: a project KB for systems and site-specific records
# then add "project": ["db_project"] to lessons-kb.json
kbcli profiles                     # built-in and project profiles
kbcli similar --profile generic -i formula=H2O -i multiplicity=1 --xyz water.xyz
kbcli add-lesson -f lesson.json    # GUIDE §3-§5
```
`lessons-kb.json` is found by searching upward from the working directory. It names the shared KB, the project KBs, the project's own profile directories, and where copies of cited files go:
```json
{"shared": "db", "project": ["db_project"], "profiles": ["profiles"], "supplements": "supplements"}
```

## Using it with Claude Code
In the project's always-loaded instructions (`CLAUDE.md` or `AGENTS.md`):
- name where the KB is and where this framework is;
- tell agents to read the KB before planning a calculation, and to write to it when a piece of work closes, following GUIDE §1 and §11;
- say that the KB only grows in normal sessions, and contradictions go in as conflicts (GUIDE §4 rule 5, §12).

Link the agent and the skills into the user or project Claude configuration:
```bash
ln -s <checkout>/agents/kb-reconciler.md ~/.claude/agents/kb-reconciler.md
ln -s <checkout>/skills/continue-doc ~/.claude/skills/continue-doc
ln -s <checkout>/skills/slurm-chain ~/.claude/skills/slurm-chain     # site notes: SLURM_SITE.md, from the project
```

## Contents

| Path | What it is |
|---|---|
| `src/lessons_kb/` | the library: `core` (data model, validation, locking, routing, conflicts), `profile`, `geometry`, `supplements`, `config`, `cli` |
| `src/lessons_kb/profiles/generic/` | the built-in profile, with its README, calibration and tests |
| `docs/GUIDE.md` | the rules: data model, writing, CLI, storage, conflicts and reconciliation, writing a profile |
| `agents/kb-reconciler.md` | Claude Code agent for reconciliation sessions |
| `skills/continue-doc/`, `skills/slurm-chain/` | Claude Code skills: continuation documents for multi-session work; smoke-gated Slurm job chains |
| `tests/test_core.py` | `python tests/test_core.py` |
| `kbcli.py` | runs the CLI from a checkout without installing |
