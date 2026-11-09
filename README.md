# lessons-kb

Repository: https://github.com/joshz793/KB_Frame · current release: `v0.3.1` · license: MIT

A lessons-learned knowledge base for computational work. People and AI agents (e.g. Claude Code) use it to record:
- the systems they studied;
- the models they tried on each;
- the transferable lessons they learned.

They read it before planning the next calculation. Records are content-hashed, so knowledge bases can be split, merged and shared. A **profile** per class of systems defines identity, a geometry-based fingerprint and similarity, so lessons reach related systems. The `generic` profile, for any molecule or cluster, is built in.

Normal sessions only add to the KB. A finding that contradicts an existing lesson is recorded as a **conflict**. Existing lessons change only in dedicated **reconciliation** sessions (`agents/kb-reconciler.md`).

The rules for readers, writers and profile authors are in [docs/GUIDE.md](docs/GUIDE.md).

## Features

**Records**
- **Systems, models and lessons.**
  - A *system* is a chemical system, identified by its profile.
  - A *model* is one way it was computed, with status and results.
  - A *lesson* is one transferable statement, with required evidence and a confidence level: established, provisional or open question.
- **Content-hashed ids.** The same record gets the same id in every copy of a KB, so KBs can be split, merged and shared without collisions.
- **Structured context on lessons and conflicts:** systems, observables, methods and conditions. This is what reconciliation compares.
- **Setup conditions.** A lesson that depends on the tools names them, e.g. `software: ["pyscf:2.6"]` or `platform: ["cluster-a"]`. Readers declare their own setup and see only the lessons that apply (`--software`, `--platform`).

**Finding what is known**
- **Similarity search** (`similar`). Systems are ranked by a profile-defined score from identity fields and a geometry fingerprint, with a per-component breakdown, and the applicable lessons are listed for each.
- **Scoped lessons.** A lesson reaches every system, one profile, a tag-defined family, or named systems (`lessons --system …`).
- **Related lessons** (`related`): lessons whose wording and scope overlap a draft, checked before writing.
- **Links both ways.**
  - `references` (builds on, supports, qualifies, see also) are written by the lesson that points.
  - `referenced_by` is derived on every load and save, from references, supersession and conflicts, so the two directions can't drift.
  - `links <lid>` shows both directions.
- **Focused copies** (`condense`): a small KB around one target system, to hand to an agent.
- **Human-readable digest** (`render`): `LESSONS.md`, with open conflicts and links.

**Writing safely**
- **Validation on every write.** Evidence is required. kbcli checks:
  - scope and tag rules;
  - normalized identities;
  - dangling references.

  It warns on a lesson that holds several statements, a tag that widens a lesson, universal wording (*every*, *all*, *always*, *never*, *must*) without listed coverage, and copied seeded text.
- **Locked, atomic writes** with automatic backups. Several sessions can write concurrently, and a failed write changes nothing and prints no success line.
- **Append-only by default, except metadata.** Normal sessions add lessons, systems and models, add to lists, and fill empty fields once. They may also edit a lesson's metadata: evidence annotations (`evidence`: kind, lines, quote, what it supports, the run), `coverage`, `verification` records (`verify`) and `reviewed`. Changing what a lesson says or where it applies, superseding, moving or removing it needs `--reconcile`.

**Shared and project knowledge**
- **Two kinds of KB, read together.**
  - A *shared* KB holds lessons that hold beyond one project, written so outsiders can use them; it is fit to publish.
  - Any number of *project* KBs hold systems, site-specific and situation-specific records.
- **Automatic routing.** Each new record goes to the right KB (`--into` and `move` override). A shared lesson may not depend on a project-only record.
- **Shard directories:** one shard per profile, plus a general one and one per site, so a KB splits cleanly.

**Conflicts and reconciliation**
- **Conflicts.** A finding that contradicts existing lessons, or two lessons that contradict each other, is recorded in `conflicts.json` (`conflict add`) instead of changing the KB. Readers see `[contested: C-…]` next to the lessons involved.
- **Reconciliation sessions** (`--reconcile`, agent `agents/kb-reconciler.md`) resolve each conflict with one action:
  - replace, with an old → new mapping that moves every reference;
  - constrain, e.g. to a software version;
  - relevel, merge, split, both hold, or reject.

  Removed lessons are archived in the conflict, and changes made without a conflict are kept as closed records, so every change is on file.

**Evidence that travels**
- **Supplements** (`supplement`): copies of the files lessons cite, plus the scripts and tables those documents name.
  - One folder per kind of KB: the shared folder (committed with the shared KB) and a project folder inside each project KB, so project evidence never travels with the shared knowledge. `--all` copies every lesson's evidence into its own folder; `--shared` and `--project` do one kind.
  - Inside a folder, copies are stored by the level a lesson applies at, with a manifest and checksums.
  - Large outputs can be stored as line excerpts.
  - `validate` reports missing copies, changed copies, and originals that changed after copying.

**Profiles** (one per class of systems)
- Each profile defines identity fields with normalizers, fingerprint descriptors, similarity components and weights, a tag vocabulary, observables and result fields.
- Calibration checks pin the similarity: `calibrate`.
- `generic`, for any molecule or cluster, is built in. A project adds its own profiles through `lessons-kb.json`. GUIDE §8 covers writing and tuning one.

**Interfaces**
- **CLI:** `kbcli` with 31 commands (`kbcli -h`), all with machine-readable output (`--json`).
- **Python library:** `lessons_kb`, with `transaction()`, `KB`, profiles, conflicts and supplements.
- **Config:** `lessons-kb.json`, found by searching upward from the working directory.
- **Moving knowledge between KBs:** `split`, `merge` (field-level, with a conflict report), `condense` and `rehash` (new ids after a profile version change).
- **Requirements:** Python ≥ 3.6 and numpy only.
- **Claude Code integration:** the `kb-reconciler` agent, for reconciliation sessions.

## Install
Python ≥ 3.6 with numpy:
```bash
pip install "git+https://github.com/joshz793/KB_Frame.git@v0.3.1"    # provides the `kbcli` command
```
Or work from a checkout without installing:
```bash
git clone https://github.com/joshz793/KB_Frame.git
python KB_Frame/kbcli.py profiles
```
A project that keeps its KB in git can pin the framework as a submodule instead:
```bash
git submodule add https://github.com/joshz793/KB_Frame.git framework
git -C framework checkout v0.3.1
```
Its own `kbcli.py` launcher then puts `framework/src` on the path (see `kbcli.py` here for the pattern), or it installs the submodule with `pip install -e framework`.

Releases are git tags (`vMAJOR.MINOR.PATCH`); the version is `lessons_kb.__version__`. Report problems and propose changes through issues and pull requests on GitHub.

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

Link the agent into the user or project Claude configuration:
```bash
ln -s <checkout>/agents/kb-reconciler.md ~/.claude/agents/kb-reconciler.md
```

## Contents

| Path | What it is |
|---|---|
| `src/lessons_kb/` | the library: `core` (data model, validation, locking, routing, conflicts), `profile`, `geometry`, `supplements`, `config`, `cli` |
| `src/lessons_kb/profiles/generic/` | the built-in profile, with its README, calibration and tests |
| `docs/GUIDE.md` | the rules: data model, writing, CLI, storage, conflicts and reconciliation, writing a profile |
| `docs/FILES.md` | every file and folder kbcli writes, when, and for which use case |
| `agents/kb-reconciler.md` | Claude Code agent for reconciliation sessions |
| `tests/test_core.py` | `python tests/test_core.py` |
| `kbcli.py` | runs the CLI from a checkout without installing |

## License
MIT; see [LICENSE](LICENSE).
