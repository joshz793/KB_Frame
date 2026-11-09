# Lessons-learned knowledge base: general guide

This guide is for agents and people who read or write the knowledge base (KB), or who add support for a new class of chemical systems.

It is domain-agnostic. Everything specific to one class of systems lives in that class's **profile** and its document, `profiles/<name>/README.md`.

Contents:
1. Purpose and obligations
2. Concepts
3. Data model
4. Rules for writing
5. CLI recipes
6. Using the library from Python
7. Split, merge, condense
8. Writing a profile
9. Profile document template
10. Seeding a KB from existing documents
11. Checklists
12. Conflicts and reconciliation

---

## 1. Purpose and obligations
The KB records what has been learned from calculations on chemical systems, so that the next calculation on the same or a similar system starts from that knowledge. It records:
- which models were tried;
- what they gave;
- what went wrong;
- which decisions were made, and why.

**Read it before planning a calculation.** That means before planning one on a new system, on a modified system, or with a modified model.
- Run `kbcli similar` with the matching profile.
- Read the lessons it lists: run `kbcli show <id>` for the full text.
- Lessons with `confidence: established` should change your plan, or you should say why they don't apply.

**Write to it when a piece of work closes.** That is after its results have been interpreted and any follow-up changes or second thoughts have settled. Record:
- the outcome of each model: finished, failed or abandoned, with its results;
- the diagnoses: why something behaves as it does;
- the user's decisions about how to work.

Interim readings that are later revised would leave records that need correcting, so keep them in the work's own notes (e.g. its continuation document) until the work closes. Cite the evidence the conclusions rest on.

**In normal sessions the KB only grows, apart from metadata.**
- You add lessons, systems and models.
- You may add to list fields, and fill an empty field or a pending model once.
- You may edit a lesson's **metadata**: the fields that say how well its claim is supported, not what it claims or where it applies. These are `evidence` (annotate an entry, or add one; never remove one or change its `path`), `coverage`, `verification` and `reviewed` (§3).
- You never change what an existing lesson says or where it applies: no editing its title, text, category, scope, `applies_to`, confidence, tags or `context`; no superseding, moving or replacing it. You never overwrite a filled field of a system or model.
- When a new finding contradicts a lesson, record it as a **conflict** instead of adding it (§4 rule 5).
- Everything else about existing lessons changes only in dedicated **reconciliation sessions** (§12): resolving conflicts, and heavy reworking such as rewording, splitting, merging and re-levelling.
- kbcli enforces this: writes run in `general` mode unless `--reconcile` is given.

**Never hand-edit the KB files (`db/*.json`, `db_project/*.json`).** Use `kbcli` or the library's `transaction()`. Both lock the KB, validate the result and keep backups, and other sessions may be writing at the same time.

## 2. Concepts

| Term | Meaning |
|---|---|
| **System** | One chemical system, as defined by its profile's identity fields. Its id is the first 12 hex digits of the sha256 of its canonical key, so the same system has the same id everywhere |
| **Model** | One way the system was computed: level of theory, model definition, status, result summary. Models belong to a system |
| **Lesson** | One transferable statement learned from work, with evidence. Lessons are separate from systems and say which systems, profiles or tags they apply to |
| **Profile** | Python code plus a document that defines, for one class of systems, the identity fields, the fingerprint descriptors, the similarity score, and the tags, categories and result fields. `generic` works for any system; specialized profiles do better for their class |
| **Shard** | Any KB file. The KB is a directory of shards (below); split, merge and condense produce and consume others |

### Storage: the shared KB and project KBs
Knowledge lives in two kinds of KB, read together:

| KB | Default location | Holds | In version control |
|---|---|---|---|
| **Shared** | `shared` in the project's `lessons-kb.json` (or `--db`, `$LESSONS_KB`) | Lessons that hold beyond one project, stated so a reader outside it can use them | Yes |
| **Project** | `project` in `lessons-kb.json` (or `--project`, repeatable; `$LESSONS_KB_PROJECT`, `:`-separated) | The systems studied and their models; lessons about one system; site (platform) lessons; the group's own scripts; situation-specific decisions; copies of its lessons' evidence in its own `supplements/` | No (it names local paths and unpublished results) |

- **Reads merge everything.** Every command reads the shared KB and all project KBs. `similar` therefore ranks the project's systems and returns both kinds of lessons. `--no-project` reads the shared KB alone.
- **Writes go back where a record came from.** A new record is routed by `kb.core.route_new`:
  - to the project KB: a system (and its models), a lesson about one system or listing systems, a lesson with a platform condition, and a `user_decision` lesson;
  - to the shared KB: everything else.

  Override the routing with `add-lesson --into shared|project` or `add-system --into …`. `move ID --to shared|project` moves an existing record.
- **The shared KB must stand alone.** A write is refused if a shared lesson would apply to a system, or be superseded by a lesson, that exists only in a project KB. `validate` warns when a shared lesson cites a file in a home directory.
- **Writing for the shared KB:** state the rule and the observation in chemistry terms ("in a capped cluster of host X, …", not the run's label). Leave out run labels, model ids, project script and directory names, environment variables and lesson ids. Cite the project files in `evidence`, which may point anywhere the group can read.
- **A methodological user decision** that is good practice anywhere goes to the shared KB (`--into shared`), restated as practice. A decision for one situation goes only into the system's `user_decisions` (§4 rule 4).

### Supplements: copies of the cited files
Copies of the files lessons cite, so their evidence can be read where the originals can't. **Each kind of KB has its own supplements folder**, and a lesson's copies go into the folder of the KB that stores it:

| Folder | Default location | Holds copies for | In version control |
|---|---|---|---|
| Shared | `supplements/` beside the shared KB | shared lessons | yes, with the shared KB |
| Project | `supplements/` inside each project KB directory (`<name>.supplements/` beside a single-file KB) | that KB's lessons | no, with the project KB |

`supplements` in `lessons-kb.json` sets the locations: a path sets the shared folder; `{"shared": …, "project": …}` sets both (one project folder then serves every project KB). A file cited by lessons in both KBs is copied into both folders, so each KB stands alone.
- **Commands:** `supplement --all` copies the evidence of every lesson, each into its own folder; `--shared` and `--project` do one kind; `supplement <lid>…` does the lessons named.
- **Moving between folders:** when a lesson moves between KBs (`move`), or a copy sits in the shared folder from an earlier version, the next `supplement --all` copies it into the right folder, from the original or, if that is gone, from the old copy. The shared folder keeps its copy until then. `validate` warns while the shared folder holds copies that only project lessons cite.
- **Layout:** folders follow the level a lesson applies at, as in the lesson record (§3, "Applicability"):

  | Folder | Files cited by |
  |---|---|
  | `general/` | general lessons |
  | `profile/<profile>/` | profile lessons |
  | `family/<tag>/` | family lessons (their first tag, `:` written as `_`, e.g. `family/ligand_O/`) |
  | `system/<system alias>/` | one-system lessons |
  | `platform/<site>/` | lessons with a platform condition |
  | `<level>/refs/` | files that the documents in `<level>/` name (scripts, job files, tables) |

  - A file cited at several levels is stored once, at the broadest (general, then profile, family, system, platform).
  - Files keep their own names. Equal names from different sources get their parent folder as a prefix (`run_a__RESULTS.md`), or a short hash if those match too.
  - Every `supplement` run recomputes the layout and moves copies that are still current. A file moves up a level when a broader lesson starts citing it.
  - `MANIFEST.json` maps each copy to its source, and only files under its `base` directory are ever copied.
- **Citations:** an evidence entry with a copy reads `{"path": <original>, "note": ..., "supplement": <path inside supplements/>, "sha256": <hash of the copy>}`. The original path stays the primary reference; the copy is for readers who can't reach it.
- **Referenced files** (scripts, job files, tables named inside a cited document) are copied with `--referenced` into `<level>/refs/`. They are listed only in the manifest, with `referenced_by`, and are kept on later runs.
- **Size cap:** 1 MB per file by default. For a larger output, store the lines the lesson rests on: `supplement <lid> --path <file> --lines A-B`.
- **Checks:** `validate` warns when a copy is missing or no longer matches its checksum, and when the original has changed since it was copied (rerun `supplement`).
- Evidence entries are not part of a lesson's id, so linking copies never changes ids.
- **After adding a lesson** (shared or project), run `kbcli supplement <lid> --referenced`. [FILES.md](FILES.md) maps both folders.

[FILES.md](FILES.md) lists every file and folder kbcli writes, when, and for which use case.

Inside each KB directory, records are split into shards:

| Shard | Holds |
|---|---|
| `general.json` | Lessons whose scope is `general`, and lessons that concern no single profile. Always present |
| `<profile>.json` | The systems of that profile, and the lessons tied to that profile alone (scope `profile`, `family` or `system`) |
| `platform_<site>.json` | Lessons with a platform condition (`applies_to.platform`, §3), whatever their scope: knowledge about one site's machines, filesystems and containers |

- **Shard routing is automatic.** Every write loads all the shards, applies your change, validates the whole KB, and writes back only the shards that changed. A record that changes shard (e.g. a lesson that gains a platform condition) moves by itself.
- **Conflicts** (§12) live in `conflicts.json` inside each KB directory, beside the shards. A conflict that involves only shared lessons, with a finding that would be shared, is stored in the shared KB; any other conflict is stored in the project KB.
- **Superseded and replaced lessons:** a superseded lesson stays in the KB it was in. A lesson removed by `replace` is archived inside the conflict that removed it. When that conflict is stored in a project KB and the lesson was shared, the shared KB also gets a closed `direct` record holding the archived copy and naming its shared successors (field `trace_of`), so the shared KB still explains where the lesson went. Resolving does this automatically; `conflict trace` adds the record for conflicts resolved before 0.3.1.
- **Bringing in another group's KB:** add it as another project KB with `--project PATH` (or `$LESSONS_KB_PROJECT`), or `merge` it into a file.
- Backups of each shard (the last 20) go to `backups/` inside each KB directory.

## 3. Data model
The top level is `{"kb_meta": {...}, "systems": {id: system}, "lessons": {id: lesson}}`.

### System (keys outside this list are rejected)

| Field | Content |
|---|---|
| `id`, `canonical`, `alias` | Derived from the identity by the profile. `alias` is for display |
| `profile`, `profile_version` | Which profile defines the identity |
| `identity` | The profile's identity fields, in normalized form |
| `fingerprint`, `fingerprint_inputs` | Descriptors computed by the profile, and the inputs (e.g. file paths) they came from |
| `tags` | Strings, optionally `family:value`. The profile adds suggested tags; you can add more |
| `reference_data` | A free-form dict: pointers to experimental or benchmark data plus a short summary. Point to data files; don't copy large tables |
| `models` | A list of models (below) |
| `best_model` | The id of the model currently recommended, or null |
| `open_questions` | A list of strings |
| `user_decisions` | A list of `{text, date, by?, evidence?}`. Decisions the user made about this system |
| `docs` | Paths of documents with more detail (hand-off notes, results tables) |
| `review` | `{level: full or stub, date, by?, note?}`. Use `stub` when the entry was filled from documents without a full review |
| `notes` | Free text |
| `created`, `updated` | Set automatically |

### Model (inside `system.models`)

| Field | Content |
|---|---|
| `id` | Unique within the system, e.g. `M1` or a short slug. **Required** |
| `status` | **Required.** One of `planned`, `pending`, `running`, `done`, `failed`, `abandoned` |
| `dir` | Run directory |
| `level_of_theory` | One line |
| `model_details` | dict or str: what is in the model and how each part is treated |
| `purpose` | Why it was run: the question it answers |
| `jobs` | Scheduler job ids |
| `result_summary` | dict. The profile's `result_fields` lists the expected keys |
| `docs`, `notes`, `date`, `updated` | |

### Lesson

| Field | Content |
|---|---|
| `id` | `L-` + sha256(category\|title\|text\|date)[:10], set automatically. **Title, text, category and date are fixed; a lesson changes only in reconciliation (§12), by replacement** |
| `title` | One line, stating the lesson, not the topic ("X fails when Y", not "about X") |
| `text` | 1–4 sentences: the finding, the numbers that support it, and what to do about it. `validate` warns above 600 characters |
| `category` | A core category (below) or one added by a profile listed in `applies_to.profiles` |
| `scope` | `general`, `profile`, `family` or `system` (see "Applicability") |
| `applies_to` | `{profiles: [...], systems: [ids], tags: [...], software: [...], platform: [...]}`. The last two are the optional **setup condition** (see "Applicability") |
| `evidence` | **Required, non-empty**: `[{path, note, ...}]`. `path` is a file that supports the lesson (a result table, log, document or script); `note` says where in it to look. Optional keys make the claim checkable: `kind` (`output`, `input`, `analysis`, `note` or `literature`), `lines` (`"A-B"`, the lines the claim rests on), `quote` (the text at those lines, verbatim), `supports` (which claim or number the entry backs) and `model` (`<system id>/<model id>` of the run). `supplement` and `sha256` are written by `kbcli supplement`. **Metadata:** editable in normal sessions (`kbcli evidence`); entries are never removed and their `path` never changes |
| `confidence` | `established` (reproduced, or directly measured), `provisional` (one observation, or plausible but unconfirmed) or `open_question` (a hypothesis to test) |
| `source` | `computed`, `user_decision` or `literature` |
| `status`, `superseded_by` | `active` or `superseded`, with the id of the replacing lesson, or a list of ids when the lesson was split |
| `author`, `date`, `updated` | `date` is the date of the finding: when the evidence was produced or the decision made. Use the recording date only if the evidence gives none. It is part of the id, so a fixed date keeps seeded ids stable. `updated` is set automatically |
| `seeded_from_docs` | True when the lesson was copied from an existing document, not observed directly |
| `reviewed` | True only after a person has confirmed it. **Metadata** |
| `coverage` | Optional: the cases the evidence covers, one string each, e.g. `["singlet", "triplet", "quintet"]`. Needed when the lesson uses a universal word (§4 rule 9). **Metadata**; not part of the id |
| `verification` | Optional: the checks of the lesson's claims against its evidence, newest last: `[{"date", "by", "method": "agent"\|"person", "verdict", "note"?}]`, where `verdict` is `supported`, `partly`, `not_supported` or `cannot_verify`. Add one with `kbcli verify LID --verdict …`. `render` shows the latest. **Metadata**; not part of the id |
| `tags` | Free tags on the lesson itself, for searching |
| `references` | Optional links from this lesson to others: `[{"id": "L-…", "rel": …, "note"?: …}]`, where `rel` is `builds_on`, `supports`, `qualifies` or `see_also` (the default). A new lesson may point at existing ones in any session; links between existing lessons are added in reconciliation. Not part of the id |
| `referenced_by` | **Derived; never written by hand.** Who points at this lesson: other lessons' `references` (with their `rel`), lessons it supersedes (`superseded`), and conflicts that name it (`contested` while open, `reconciled` once closed). Recomputed on every load and save, so the two directions can't drift. A stored file lists only the links within that file; `kbcli links <lid>` shows all of them |
| `context` | **Required on new lessons.** The structured setting reconciliation compares (§12): `{"systems": [...], "observables": [...], "methods": [...], "conditions": [...]}`, each a list of short strings. Write `systems` as hosts or ions in words, not ids. Take `observables` from the profile's list (`kbcli profiles NAME`), adding a definition in parentheses where it matters, e.g. `"MAD (Z1-Z5 /5)"`. `methods` holds level of theory, basis, boundary, and software with version; `conditions` says when the finding holds. Not part of the id |

**Core categories** (`kbcli categories`): `model_definition`, `method`, `basis`, `active_space`, `environment`, `geometry`, `convergence`, `analysis`, `reference_data`, `resources`, `software`, `workflow`.

### Applicability (how a lesson finds the systems it concerns)
A lesson applies to system S if **any** of these hold:
- S's id is in `applies_to.systems`.
- `scope = general`: it applies to every system of every profile.
- `scope = profile` and S's profile is in `applies_to.profiles`.
- `scope = family`, and one of `applies_to.tags` matches a tag of S. A lesson tag `t` matches a system tag `t` or `t:anything`. If `applies_to.profiles` is given, S must also belong to one of them.

**Tags are alternatives, not conditions.** A family lesson reaches a system that has *any one* of its tags, so every extra tag widens the lesson.
- To say "oxide-ligand lanthanide sites", give only the tag that names that class (`ligand:O`), not `lanthanide` + `ligand:O`; the second form reaches every lanthanide.
- Only `applies_to.profiles` narrows: S must belong to one of them as well.
- If no single tag names the class, list the systems in `applies_to.systems`, or have the profile add a tag for the class.
- `validate` warns when one tag of a family lesson matches every system another of its tags matches, and more. It is fixed in reconciliation (§12) with `--reconcile update-lesson <lid> -f fields.json --replace`, which replaces `applies_to` instead of extending it; the id stays the same. A new lesson should simply be written with the one right tag.

Validation rules:
- `scope = system` needs `applies_to.systems`.
- `scope = profile` needs `applies_to.profiles`.
- `scope = family` needs `applies_to.tags`.

**Setup condition (software and platform).** Scope answers *which chemistry* a lesson concerns. Whether it also depends on the tools is a separate question, answered by `applies_to.software` and `applies_to.platform`:
- **`software`:** codes and libraries, optionally with a detail after a colon, e.g. `pyscf`, `pyscf:siso`, `mrh`, `openmolcas:single_aniso`, `hdf5`.
- **`platform`:** machine, filesystem or container runtime, e.g. `cluster-a`, `cluster-a:scratch`, `podman`.
- Values are lower-case tokens without spaces; kbcli normalizes them.
- **Versions** go after a colon, `software: ["pyscf:2.6"]`. That matches a reader who declares `pyscf` or `pyscf:2.6`, but not `pyscf:2.7`, so it confines a lesson to one version.
- **Matching:** `x` and `x:detail` match each other. Within a key any value matches; across keys all must.

The reader declares a setup with `--software` / `--platform` (or `$LESSONS_KB_SOFTWARE` / `$LESSONS_KB_PLATFORM`, comma-separated). A lesson with a setup condition is then:
- **shown** when the declared setup matches it;
- **dropped** when a declared key conflicts with it (e.g. the lesson says `software: [pyscf]`, the reader declared only `orca`);
- **listed separately** as "conditional on a setup you did not declare" when the reader did not declare that key.

Lessons without a setup condition always apply.

## 4. Rules for writing
1. **Evidence or nothing.** Every number and claim in a lesson must be traceable to its `evidence`. Do not write lessons from memory or by analogy. A hypothesis is allowed only with `confidence: open_question`.
2. **Choose the broadest scope that is true, then the setup condition.**
   - **Scope is about chemistry.** Ask: "Would this change a decision on a *different* system?" If yes, use `family` (with tags that describe the class) or `profile`, or `general` if it holds whatever the chemistry. Keep `system` for facts that only matter for that system.
   - **Setup is about tools.** Ask: "Would this still be true for someone using other software, or another machine?" If not, add `applies_to.software` and/or `applies_to.platform`.
   - Don't use `profile` (or `family`) scope to stand in for a software dependence. A code quirk reaches every profile whose users run that code, and nobody who runs something else.
   - Every lesson in category `software` needs `applies_to.software`; `validate` warns otherwise. Lessons in `resources` usually need a platform.
   - One observation stays `provisional` even when its scope is broad.
3. **One lesson, one statement.** Split compound findings. Make the text actionable: what to do, or avoid, and when.
   - `validate` warns when an active lesson's text is over 600 characters. That usually means several statements, or caveats that piled up over revisions. Split it (rule 5).
4. **Record user decisions twice.** Add them to the system's `user_decisions`. Also add a lesson with `source: user_decision`, when the decision is a working rule about the science that should carry over, such as "experiment, not a higher level of theory, is the reference".
   - **Standing computing rules are not lessons.** Rules set by the user or the site about how and where to compute (job-length limits, which build of a code to use, job-chain practice, login-node use) belong in the agent instructions the project always loads (its AGENTS.md, CLAUDE.md or skills). A KB copy would drift from them and would reach readers on other setups.
   - Observations about tools stay in the KB, with a setup condition: what failed, the symptom, the workaround.
   - **Don't generalize an instruction the user gave for one situation.** "Stop this run" or "drop that variant" is a decision about the case at hand. Record it in the system's `user_decisions` only, worded for that case. The findings that prompted it can still become computed lessons, with their own scope and confidence.
   - **When it is unclear whether the user meant a decision generally, ask before writing a decision lesson.** For example: "Should this apply to future work on other systems, or only to this run?" Record the answer with the decision.
5. **Never change an existing lesson; record conflicts.**
   - **Check the related lessons before every write.** Run `kbcli related -f draft.json`, which lists the active lessons whose wording and applicability overlap the draft. `add-lesson` prints the same list after a write. For each, ask whether the draft contradicts it, makes it stale or repeats it.
   - **If the draft contradicts, corrects or makes stale an existing lesson,** don't add it. Record it with `kbcli conflict add -f draft.json --contradicts LID… --why "<what disagrees>"`, giving the draft's `context`. This applies whoever wrote the old lesson. A new user decision especially needs the check, because older lessons may still recommend what the decision rules out.
   - **If two existing lessons contradict each other,** record an internal conflict: `conflict add --contradicts LID LID… --why … --context-file ctx.json`.
   - **If the draft repeats a lesson with new evidence,** record it the same way. The reconciler merges the evidence.
   - **Otherwise** add the lesson.
   - Readers see `[contested: C-…]` next to a lesson with an open conflict. Weigh both sides, read `conflict show C-…`, and say which you follow.
   - In reconciliation sessions (§12) a corrected lesson is written as a **restatement, not an append**: as if it were new, from all the evidence now available, old and new. Drop or correct the claims the new evidence weakens. Don't keep an outdated headline with caveats after it, and don't carry forward advice that hasn't been re-checked.
   - **Keep provenance honest.** A replacement that reuses text from a seeded, unreviewed lesson keeps `seeded_from_docs: true`. `validate` warns when a replacement copies such sentences without the flag.
6. **Be honest about review state.**
   - Lessons copied from documents get `seeded_from_docs: true, reviewed: false`.
   - Systems filled from documents get `review.level: stub`.
   - Use `review.level: full` only after you have read the primary results and checked the entry against them.
7. **Paths.** Use absolute paths in `evidence`, `docs` and `dir`. Use paths that exist when you write them.
8. **Close out models.** When the work closes (§1), fill each model's `status` and `result_summary` with `add-model ID -f model.json --update`, and set `best_model` if it is empty. A model registered at submission stays `pending` until then. A model is filled once: a filled result or a final status is not overwritten, so a rerun is a new model.
9. **Claim only the coverage you have.** A lesson that says *every*, *all*, *always*, *never* or *must* about its findings ("holds for every spin state") needs evidence for each case. List the cases the evidence covers in `coverage`; otherwise narrow the wording.
   - `validate` warns on an active lesson that uses one of these words without `coverage`. Hyphenated compounds (`all-electron`) don't count.
   - Normal sessions can add `coverage`: `update-lesson ID -f cov.json`, with `{"coverage": [...]}` in the file. Narrowing the wording is a reconciliation step.
   - The warning can't tell a finding from an instruction ("check every active orbital"). For an instruction, leave it; the reconciler can reword it if it overstates.

## 5. CLI recipes
Run everything as `kbcli [--json] <command>`. `kbcli` comes with `pip install <framework>`; without installing, run `python kbcli.py` from the framework checkout.
- **Where the KB is:** kbcli reads the project's `lessons-kb.json`, found by searching upward from the working directory (or `$LESSONS_KB_CONFIG`). It names the shared KB, the project KBs, the project's own profile directories and the supplements folder, with paths relative to the file:
  ```json
  {"shared": "db", "project": ["db_project"], "profiles": ["profiles"], "supplements": "supplements"}
  ```
  `supplements` may instead be `{"shared": "supplements", "project": "db_project/supplements"}`; the project folder defaults to `supplements/` inside each project KB (§2).
  `kbcli init` in an empty directory writes this file and an empty shared KB `db/`; `kbcli --db db_project init` adds a project KB. Overrides: `--db` or `$LESSONS_KB` for the shared KB (then no project KB is read unless given), `--project` or `$LESSONS_KB_PROJECT` for project KBs, `--no-project`. `--db` also accepts a single KB file, e.g. one written by `condense`.
- `--json` gives machine-readable output.
- Any python with numpy works.

```bash
# discover
kbcli profiles                      # list profiles; `profiles NAME` shows identity fields, tags, categories
kbcli categories
kbcli lookup <id or alias substring>
kbcli show <system id/alias | lesson id>

# before planning: what do we know about systems like this one?
kbcli key --profile generic -i formula=H2O -i multiplicity=1          # canonical key and id
kbcli similar --profile generic -i formula=H2O -i multiplicity=1 --xyz geom.xyz --top 5
kbcli similar --system <id>                                           # neighbours of a known system
kbcli lessons --system <id>          # every lesson that applies to it
kbcli lessons --profile generic --category convergence
kbcli similar --system <id> --software pyscf,mrh --platform cluster-a   # declare your setup (§3)

# after results
kbcli add-system -f system.json      # {"profile":..., "identity":{...}, "inputs":{...}, "tags":[...], ...}
kbcli add-system -f system.json --update   # merge new fields into an existing system
kbcli update-system <id> -f fields.json    # e.g. {"best_model": "M2", "open_questions": [...]}
kbcli add-model <id> -f model.json [--update]
kbcli add-lesson -f lesson.json      # object or list; prints the new ids and which KB holds them
kbcli add-lesson -f lesson.json --into shared   # override the routing (§2)
kbcli --reconcile move <id> --to shared|project  # move an existing record (new ones: add-lesson --into)
kbcli conflict add -f draft.json --contradicts <lid>... --why "..."   # a finding that contradicts lessons (§12)
kbcli conflict add --contradicts <lid> <lid>... --why "..." --context-file ctx.json   # lessons that contradict each other
kbcli conflicts --open                 # open conflicts; `conflict show C-...` for one
kbcli links <lid>                      # everything a lesson points to and everything pointing to it
kbcli --reconcile conflict resolve C-... --action replace --result <new> --replace <old>   # reconciliation only
kbcli --reconcile conflict resolve C-... --action replace --result <n1> <n2> --replace <o1> <o2> --map <o1>=<n1> <o2>=<n2>
kbcli --reconcile remove <lid> --why "..."   # drop a lesson without a successor (archived in conflicts.json)
kbcli --reconcile conflict trace           # copy shared lessons archived only in project-KB conflicts into the shared KB
kbcli supplement <lid> [--referenced]     # copy the files a lesson cites into its KB's supplements folder (§2)
kbcli supplement --all --referenced       # every lesson, each into its own folder; files over --max-size (1M) are skipped
kbcli supplement --shared | --project     # only the shared, or only the project, lessons
kbcli supplement <lid> --path out.log --lines 600-640   # an excerpt of a large output
kbcli evidence <lid> --path out.log --kind output --lines 600-640 --quote "..." --supports "the 4 macro"   # metadata, any session
kbcli verify <lid> --verdict partly --method agent --by <name> --note "..."   # record a check of the claims (metadata)
kbcli update-lesson <lid> -f meta.json   # metadata only in normal sessions: evidence, coverage, verification, reviewed
kbcli --reconcile update-lesson <lid> -f fields.json [--replace]   # any field but the id-defining ones; --replace: applies_to is replaced
kbcli related -f draft.json          # before writing: active lessons the draft may contradict or repeat
kbcli related <lid>                  # the same for a lesson already in the KB
kbcli --reconcile supersede <old lid> --by <new lid>            # reconciliation only (§12)
kbcli --reconcile supersede <old lid> --by <new lid> <new lid>  # a split that keeps the old lesson visible
kbcli validate [--strict]            # --strict: dangling references are errors
kbcli render                         # rewrites LESSONS.md (human digest) next to the DB
```

Example `lesson.json`:
```json
{"title": "Method X overestimates property Y by about 20% for systems with feature Z",
 "text": "In three systems with Z, X gave Y 18-24% above the reference (table). Scale or use method W.",
 "category": "method", "scope": "family", "applies_to": {"profiles": ["generic"], "tags": ["feature_z"]},
 "evidence": [{"path": "/abs/path/results.csv", "note": "rows 3-5"}],
 "confidence": "provisional", "source": "computed", "author": "agent session 2026-10-26"}
```

Example `system.json` (the identity fields and inputs come from the profile; see `kbcli profiles NAME`):
```json
{"profile": "generic", "identity": {"formula": "H2O", "multiplicity": 1, "name": "water"},
 "inputs": {"xyz": "/abs/path/water.xyz"}, "tags": ["small_molecule"],
 "reference_data": {"pointer": "/abs/path/reference.json#water", "summary": "..."},
 "docs": ["/abs/path/HANDOFF.md"], "review": {"level": "stub", "date": "2026-10-26"}}
```

## 6. Using the library from Python
```python
import sys; sys.path.insert(0, '/path/to/lessons')
from kb.core import transaction, build_system, KB
from kb.profile import load_profiles
P = load_profiles()['generic']
with transaction([('shared', '/path/to/lessons/db'), ('project', '/path/to/lessons/db_project')]) as kb:
    # lock -> load both KBs -> (your edits) -> validate -> each record back to its KB, new ones routed (§2)
    rec = build_system(P, {'formula': 'H2O', 'multiplicity': 1}, {'xyz': '/abs/water.xyz'}, tags=['small_molecule'])
    if rec['id'] not in kb.systems:
        kb.add_system(rec)
    kb.add_model(rec['id'], {'id': 'M1', 'status': 'done', 'level_of_theory': '...'})
    lid = kb.add_lesson({...})
```
- If anything inside the `with` block raises, including the final validation, the file is not changed.
- `transaction(..., mode='general')` is the default: existing lessons can't be edited, superseded, moved or replaced, and filled fields can't be overwritten. Pass `mode='reconcile'` only for reconciliation (§12). `kb.add_conflict(finding_or_None, contradicts, why, context=..., evidence=...)` works in both modes. `kb.adopt_finding`, `kb.replace` and `kb.resolve_conflict` need `reconcile`; `kb.contested(lid)` lists a lesson's open conflicts.
- `transaction(path)` with a single path works on one KB alone. `kb.place(id, 'shared'|'project')` moves a record; `kb.role_of(kind, id)` says where it is stored.
- Read-only use: `KB.load(path)` or `KB.load_sources([(role, path), ...])`, `kb.find()`, `kb.similar(query)`, `kb.lessons_for(system=..., setup={'software': [...], 'platform': [...]})`.
- `kb.core.setup_match(lesson, setup)` returns True (applies), False (conflicts with the setup) or None (conditional on an undeclared key).
- Similarity queries take a partial record: `build_system(P, identity, inputs, partial=True, tags=[...])`.
- `kb.core.make_lesson(d)` returns the completed lesson, including its id, without adding it. Use `make_lesson(d)['id'] in kb.lessons` to skip lessons that already exist, which keeps seed scripts idempotent. A fixed `date` in `d` keeps the id stable across reruns.
- `kb.update_system(id, fields)` sets top-level system fields; identity fields can't be set this way.
- `kb.update_lesson(lid, fields, replace=False)` accepts every field except `id`, `title`, `text`, `category`, `date` and `author`. `evidence` and `applies_to` are extended unless `replace=True`, which replaces `applies_to`.
- `kb.supersede(old, new)` takes one id or a list of ids (a split). `kb.core.successors(lesson)` always returns a list.
- `kb.related_lessons(lesson_or_draft, top=8)` returns `[(score, lesson)]` for the active lessons most likely to overlap.

## 7. Split, merge, condense
Ids are content-derived, so the same system or lesson has the same id in every shard.

| Command | What it does |
|---|---|
| `split --by profile\|system\|tag\|category --out DIR [--no-general]` | Writes self-contained shards. Each holds its systems and every lesson that applies to them; general lessons go into every shard, or only into `_general.json` with `--no-general`. Lessons that apply to nothing go to `_unassigned.json`. Category splits put all systems into `_systems.json`. **Merging all the shards gives back the original** |
| `merge A.json B.json DIR/ … --out C.json [--prefer newer\|first\|last]` | Unions systems and lessons by id. A record present in several shards is merged field by field: lists of `{id: ...}` items are unioned by id, other lists by value, dicts recursively, and empty values never conflict. Differing scalars are reported as conflicts and resolved by `--prefer` (default: the record with the later `updated`). Superseding is monotonic, and the successors of a lesson superseded in both inputs are unioned |
| `condense (--system ID \| --profile P -i … --xyz …) [--min-score 0.5] [--top N] --out F` | A small KB for a task whose target is known: the systems similar to the target (same profile, score ≥ threshold), every lesson that applies to them or to the target, its profile's lessons and the general lessons. Give this file to an agent, with `--db F`, instead of the whole KB; it can be merged back later |
| (every command) | Accepts a shard directory or a single file as `--db`. On a directory, writes are routed to shards as in §2 |

## 8. Writing a profile
Add a profile when `generic` cannot tell apart the systems you care about, or cannot rank them usefully.

### Files

| Path | Content |
|---|---|
| `profiles/<name>/profile.py` | Defines `PROFILE = <Subclass of kb.profile.Profile>()`. `PROFILE.name` must equal `<name>` |
| `profiles/<name>/README.md` | The profile document, following the template in §9. This is the specialized guide for the class |
| `profiles/<name>/calibration.json` | Labelled similarity checks (format below) |
| `profiles/<name>/test_profile.py` | unittest: identity normalization, fingerprint invariance, calibration |
| `profiles/<name>/examples/` | Optional small inputs used by the tests and calibration |

Profiles are found automatically. Directories listed in `$LESSONS_KB_PROFILES` (separated by `:`) are searched as well.

### API (`kb/profile.py`)
```python
from kb.profile import Profile, Field
from kb import geometry            # parse_formula, hill_formula, reduce_counts, read_xyz, element_of,
                                   # pair_distances, radial_shells, kernel_similarity, as_weighted,
                                   # shells_as_weighted, composition_similarity
class MyProfile(Profile):
    name, version, description = 'my_profile', '1', '...'
    fields = [Field('a', 'str', normalize=fn, doc='...'),            # kinds: str int float dict list
              Field('b', 'int', default=0),
              Field('c', 'str', required=False),                      # omitted -> not in the key
              Field('label', 'str', required=False, in_key=False)]    # descriptive only
    fingerprint_inputs = {'xyz': 'what the input is'}
    components = {'x': 0.5, 'y': 0.5}        # implement sim_x(a, b), sim_y(a, b) -> float in [0, 1] or None
    tag_vocabulary = {'family:': 'meaning of family:<value> tags', 'flag': 'meaning'}; strict_tags = True
    lesson_categories = {'my_category': 'meaning'}
    result_fields = {'key': 'meaning'}
    def alias(self, ident): ...                         # short display name
    def fingerprint(self, ident, inputs): ...           # JSON-serializable dict
    def suggest_tags(self, ident, fp): ...              # tags derivable from identity/fingerprint
    def sim_x(self, a, b): ...                          # a, b are system records (use a['fingerprint'], a['identity'])
    def validate_system(self, system): ...              # optional: return (errors, warnings); call super()
PROFILE = MyProfile()
```
Behaviour supplied by the base class:
- **`canonical()`** is `profile=<name>;f1=v1;...` over the in-key fields in declaration order, and the id is its sha256[:12].
- **`similarity()`** is the weighted mean of the components. It returns the score and a per-component breakdown.
  - A component that returns `None` because one record lacks the data counts as `missing_value` (default 0.5, neutral). Dropping it would judge sparse records on the remaining, usually lenient, components, so they would outrank fully described ones.
  - A component the *query* cannot supply at all (e.g. tags on an untagged query) is dropped. It would be constant across candidates.
  - Set `missing_value = None` in a profile to drop missing components instead.
- **`sim_tags()`** is ready to use as a component: tag Jaccard, with half credit for a shared `family:`. It returns `None` when either side has no tags.
- **Queries may be partial**, so a component must return `None`, not fail, when its data is missing.

### Tuning procedure

**1. Decide what "the same system" means for the questions being studied.**
- These choices become the identity (in-key) fields. Include exactly what changes the answer.
- Leave out how it was computed (method, basis, model size); that belongs in `models`.
- Leave out whatever is only descriptive (`in_key=False`).
- Test: two entries should share an id exactly when one set of results and lessons could be pooled for both.

**2. Write normalizers** so that every equivalent spelling gives the same key:
- formula order and reduction;
- equivalent notations for one symbol or label;
- case and whitespace;
- units.
- Reject invalid input with `ValueError`.

**3. Choose fingerprint descriptors.** They should capture what makes lessons *transfer* between systems, for example:
- local structure around the part that matters;
- composition;
- symmetry;
- electronic character.

They must be invariant to anything that does not change the system (orientation, atom order, file format). Prefer compact, interpretable descriptors (e.g. distance shells) over raw coordinates, and reuse `kb/geometry.py`.

**4. Define similarity components, then calibrate the weights.**
- Write one `sim_<name>` per aspect, each returning a value in [0, 1].
- Start with equal weights.
- List labelled checks in `calibration.json`: pairs that must score ≥ `thresholds.similar` or < `thresholds.dissimilar`, and orderings (the query must be closer to A than to B). Take them from systems that are understood, and include deliberate near-misses.
- Adjust the weights or components until `kbcli calibrate --profile NAME` passes.
- Record the rationale for the final weights in the profile document.

**5. Fix the vocabulary.**
- The tag vocabulary covers families and flags; use `strict_tags = True` once it is stable.
- Add extra lesson categories only for concerns the core categories don't cover.
- Set `result_fields` to the keys of `result_summary`.

**6. Version.**
- Bump `version` whenever `canonical()`, a normalizer or the in-key field list changes, then run `kbcli rehash`. It recomputes ids, updates lesson references and logs old → new ids.
- Fingerprint or weight changes do not change ids; recompute fingerprints with `rehash` too.

**7. Test and document.**
- `test_profile.py` must cover: equivalent spellings → one id; changing any in-key field → a new id; missing required fields rejected; fingerprint invariance (e.g. a rotated and permuted copy of an input); every calibration check.
- Write `README.md` from the template in §9.

### `calibration.json` format
```json
{"thresholds": {"similar": 0.7, "dissimilar": 0.5},
 "items": {"name1": {"identity": {...}, "inputs": {"xyz": "examples/a.xyz"}, "tags": ["..."]},
           "name2": {"system": "<id or alias of a system already in the KB>"},
           "name3": {"identity": {...}, "partial": true}},
 "pairs": [{"a": "name1", "b": "name2", "expect": "similar"}],
 "orderings": [{"query": "name1", "closer": "name2", "farther": "name3"}]}
```
- Relative input paths are resolved against the profile directory.
- An item that references KB systems (`"system": ...`) can only be checked once those systems are in the KB. Before that, `check_calibration` reports it as skipped (`ok = None`, `SKIP` in `kbcli calibrate`), not failed. Prefer inline items, with small inputs under `examples/` written by a script that names its sources, so calibration runs anywhere.

## 9. Profile document template (`profiles/<name>/README.md`)
```markdown
# Profile `<name>` (version N)
## Scope            which systems belong here, and which do not (use which other profile then)
## Identity         table: field | kind | required | in key | normalization | why it is part of identity
## Fingerprint      inputs expected, descriptors stored, invariances, limits
## Similarity       components and weights, with the rationale; what scores near 1, ~0.5, ~0
## Calibration      the labelled checks and current results (`kbcli calibrate --profile <name>`)
## Tags             vocabulary (families and flags) and when to apply each
## Observables      the profile's `observables` list (for `context.observables`), with any definitions they need
## Lesson categories   extra categories (if any) and when to use them
## Result fields    keys of model result_summary, with units and conventions
## Data sources     where the systems, results, references and documents of this class live
## Seeding notes    how the initial entries were created, what is stub vs full
## Known limitations   what the identity or similarity cannot capture yet; ideas for version N+1
```

## 10. Seeding a KB from existing documents
1. **Inventory first.** List the systems, their run directories, result tables and the documents that state findings.
2. **Write a seed script** at `seed/seed_<profile>_<YYYYMMDD>.py`. It builds every system, model and lesson in one `transaction()`, so the seed is atomic.
   - Make it **idempotent:** skip lessons whose id already exists, and only *add* systems and models that are missing. That way it can be rerun after fixes to the script.
   - **Never overwrite existing records from a seed.** Once installed, the live KB is newer than the seed: other sessions add models, decisions and questions. A seed that replaces fields or models erases them. Make later corrections with a separate, dated script (`seed/<what>_<YYYYMMDD>.py`) that edits specific records.
   - Keep it, for provenance.
3. **Separate the levels of review.**
   - Systems you verified against primary results: `review.level: full`.
   - Everything filled from documents only: `stub`.
   - Lessons copied from documents: `seeded_from_docs: true, reviewed: false`, with the document as evidence.
4. **Do not invent.** If a document does not say something, leave it out or record it as an open question.
5. **Re-scope while seeding.** Documents usually state findings for one system. When a finding is evidently transferable, scope the lesson broadly (`family`/`profile`/`general`), keep `confidence` honest, and cite the system-specific evidence.
6. **Check.** Finish with `validate --strict`, `calibrate`, the profile tests, and `render`; then spot-check `similar` on a few systems.

## 11. Checklists
**Before planning a calculation**
- `similar` with the matching profile, top 5, declaring the software and platform you will use.
- `lessons --system` for the closest hits.
- Read every `established` lesson in `model_definition`, `method`, `convergence`, `resources` and `software`.
- Note which lessons you are following, and why you are setting any aside.

**When closing a piece of work**
- Fill the model's `status` and `result_summary` (once).
- Add the lessons, with evidence, `context`, the broadest true scope and, when a lesson depends on the tools, a setup condition.
- Point each evidence entry at the converged run that shows the claim, with `lines` and `quote` where you can. List `coverage` when a lesson says *every*, *all*, *always*, *never* or *must* (§4 rule 9).
- Check where each landed (`add-lesson` prints it). A lesson that holds beyond the project belongs in the shared KB, written without project labels (§2). Copy each lesson's evidence with `supplement <lid> --referenced`; it goes into the folder of the KB that holds the lesson.
- Before adding each lesson, check `related`. A draft that contradicts, corrects or repeats an existing lesson becomes a conflict (`conflict add`), not a lesson (§4 rule 5).
- Record user decisions in the system's `user_decisions`. Add a decision lesson only for working rules that carry over, and ask the user if that is unclear (§4 rule 4).
- `validate`, then `render`.

**Adding a profile**
- Follow §8 steps 1–7.
- Get calibration passing.
- Write the template document.
- Seed (§10).
- Add a pointer to the new profile in `README.md`.

**Reconciliation session** (§12, agent `kb-reconciler`)
- Only when the user asks for one; every write uses `--reconcile`.
- Work through `conflicts --open`; resolve each with one action; fill `context` on every lesson touched.
- Finish with `validate --strict`, `supplement --all`, `render`.

## 12. Conflicts and reconciliation
**A conflict** is a record in `conflicts.json` (§2). It has:
- `status`: `open`, `resolved` or `rejected`;
- `kind`:
  - `finding`: a new lesson draft that contradicts existing lessons;
  - `internal`: two or more existing lessons that contradict each other;
- `contradicts`: the lesson ids;
- `disagreement`: what differs, in a sentence or two;
- `context`: the structured setting, as in §3;
- `finding`: the draft (for `kind: finding`);
- optional `evidence`;
- `resolution`, once closed: action, resulting lessons, archived lessons, note, who, date.

Any session may add a conflict, and edit lesson metadata (§1). Only a reconciliation session may resolve a conflict, or rework a lesson: reword, split, merge, re-level or remove it.

**Reconciliation sessions** are dedicated sessions, started when the user asks, and run by the agent defined in the framework's `agents/kb-reconciler.md`. Every write uses `--reconcile` (or `$LESSONS_KB_MODE=reconcile`). For each open conflict, the reconciler:
1. reads both sides, their `context`, and their evidence (supplement copies first);
2. decides whether they really disagree. Many apparent conflicts differ in a condition: system, observable definition, method, software version, or how levels were assigned;
3. chooses one action and closes the conflict with `conflict resolve C-… --action … --result LID… [--replace OLD…] --note …`.

| Action | When | What changes |
|---|---|---|
| `replace` | one lesson (often the newer) is more complete or more current | the replacement is written as a restatement. `--replace OLD` removes the old lessons and archives them in the conflict. Every reference to an old lesson (`superseded_by`, `references`, open conflicts) moves to the lesson that takes over its content. When several lessons replace several, give `--map OLD=NEW[,NEW] …`, or `--map auto` to pair each old lesson with the new one whose wording overlaps most |
| `constrain` | both hold under different conditions | `applies_to` narrowed with `update-lesson --replace` (a setup condition such as `software: ["pyscf:2.6"]`, tags or systems), and `context.conditions` stated |
| `relevel` | a lesson holds more or less broadly than its level | `scope` and `applies_to` changed; `move` between the shared and project KBs |
| `merge`, `split` | duplicates, or parts with different scopes | merged or split lessons written; the old ones replaced (`supersede OLD --by A B` keeps them visible instead) |
| `both_hold` | no contradiction once the conditions are stated | the conditions added to both lessons' `context` |
| `reject` | the finding is wrong | nothing in the KB changes; the reason goes in the note |
| `remove` | a lesson should go without a successor (e.g. on a user decision) | `--reconcile remove LID --why …` archives it in a closed `direct` record. It is refused while the lesson is another's only successor or an open conflict names it |

- `conflict adopt C-…` adds a conflict's finding to the KB as a lesson, ready for one of the actions.
- A change made without a logged conflict, e.g. on a user decision, is recorded as a closed record of kind `direct` (`kb.record_direct`). Every change to existing lessons is therefore on file in `conflicts.json`. A shared lesson removed by a record stored in a project KB is also archived in the shared KB (§2), and `validate` warns when a shared conflict names a result that the shared KB neither holds nor archives.
- Before changing a lesson, run `links <lid>` to see what points at it.
- The reconciler asks the user before acting when the choice is a scientific judgment that the evidence doesn't settle, when a lesson recording a user decision would change, or when a shared lesson would move to the project KB.
- It fills `context` on every lesson it touches.
- Unlogged contradictions it notices go in as new conflicts for the next session.
- **`validate`** reports the open conflicts. It errors on a conflict whose lessons are missing, and warns on a resolved conflict whose resulting lessons are missing.
