# Files and folders: what kbcli writes, when, and why

This is a map of every file and folder the framework creates or reads, by use case. The rules for what goes *in* the records are in [GUIDE.md](GUIDE.md); this document covers only where things land on disk.

## A typical project
After `kbcli init`, a project KB, some work, one conflict, `supplement --all` and `render`:
```
my-project/
├── lessons-kb.json            config: where the KBs, profiles and supplements are
├── LESSONS.md                 digest of the shared KB (render)
├── db/                        shared KB
│   ├── general.json           shard: general lessons (always present)
│   ├── <profile>.json         shard: one profile's systems and lessons
│   ├── platform_<site>.json   shard: lessons with a platform condition
│   ├── conflicts.json         open and closed conflicts (only once there is one)
│   ├── .lock                  lock file for writes
│   └── backups/               the last 20 versions of each shard
├── db_project/                project KB: the same files, plus
│   ├── LESSONS.md             digest of the project KB (render)
│   └── supplements/           copies of the files the project lessons cite (same layout as below)
├── supplements/               copies of the files the shared lessons cite
│   ├── MANIFEST.json
│   └── general/ profile/<p>/ family/<tag>/ system/<alias>/ platform/<site>/  (+ <level>/refs/)
├── profiles/<name>/           the project's own profiles (you write these)
└── seed/                      seed and correction scripts (you write these)
```
Both KBs are optional beyond the shared one. A KB can also be a single JSON file instead of a directory (see "Single-file KBs" below).

## Files kbcli writes

### Config
| File | Created by | Changed by | Purpose | Commit |
|---|---|---|---|---|
| `lessons-kb.json` | `kbcli init`, only when no `--db` is given and none exists | you, by hand | Names the shared KB, the project KBs, the profile directories and the supplements folder. Found by searching upward from the working directory (or `$LESSONS_KB_CONFIG`) | yes |

### Inside each KB directory (shared and project alike)
| File | Created | Changed | Purpose | Commit (shared KB) |
|---|---|---|---|---|
| `general.json` | `kbcli init` | every write that changes it | General lessons, and lessons tied to no single profile. Always present, even when empty | yes |
| `<profile>.json` | first write of a system or lesson for that profile | writes that change it; **deleted** when it becomes empty | The profile's systems (with their models) and the lessons tied to that profile alone | yes |
| `platform_<site>.json` | first lesson with an `applies_to.platform` condition for that site | as above | Site knowledge (machines, filesystems, containers), kept apart so it can be dropped when exporting | yes |
| `conflicts.json` | first `conflict add` stored in this KB, or the first change a reconciliation records | only when its content changes; never created empty | Open conflicts, resolved ones, and lessons archived by `replace` or `remove`. A shared lesson archived by a project-KB conflict also gets a trace record in the shared file | yes |
| `.lock` | first write | never (it stays empty) | Serializes writes from concurrent sessions (`flock`). Reads take no lock | no |
| `backups/<shard>.json.<timestamp>` | before a shard is rewritten or deleted | the oldest are pruned past 20 per shard | Recovery from a bad write. `conflicts.json` is not backed up | no |
| `<file>.tmp<pid>` | during a write | renamed over the target at once | Atomic writes. A leftover one means a write was interrupted; the target is intact | no |
| `LESSONS.md` (project KBs only) | `render` | each `render` | Digest of that project KB | no |
| `supplements/` (project KBs only) | first `supplement` run that copies one of its lessons | each `supplement` run | Copies of the files that KB's lessons cite (below) | no |

Which KB a new record goes to is decided by routing (GUIDE §2): systems, one-system lessons, platform lessons and `user_decision` lessons go to the project KB, everything else to the shared one. A conflict goes to the shared KB only when every lesson it contradicts is shared and its finding would be shared.

### Beside the shared KB
| File | Created | Changed | Purpose | Commit |
|---|---|---|---|---|
| `LESSONS.md` | `render` | each `render` | Digest of the shared KB. Written even when the shared KB is empty | optional |
| `supplements/` | first `supplement` run that copies a shared lesson | each `supplement` run | Copies of the files the shared lessons cite, so evidence can be read where the originals can't (below) | yes |

### Supplements
There are two kinds of supplements folder, one per kind of KB, with the same layout. A lesson's copies are in the folder of the KB that stores it; a file cited from both KBs is copied into both.

| File | Purpose |
|---|---|
| `supplements/MANIFEST.json` | Every copy, with its source path, checksums of the copy and of the source, size, date copied, and the lessons that cite it. `base` limits which referenced files may be copied |
| `<level>/<name>` | A copy of a file cited in a lesson's `evidence`, filed by the level the lesson applies at: `general/`, `profile/<profile>/`, `family/<first tag>/`, `system/<alias>/`, `platform/<site>/`. A file cited at several levels is stored once, at the broadest |
| `<level>/<stem>.L<a>-<b><ext>` | An excerpt of a large output (`supplement <lid> --path <file> --lines A-B`) |
| `<level>/refs/<name>` | A script, job file or table named inside a cited document (`--referenced`) |
| `.staging<pid>/` | Present only during a run, while current copies move to their new places |

- **Location:**
  - shared folder: `supplements` in `lessons-kb.json` (a path, or `{"shared": …}`); otherwise `supplements/` beside the shared KB (the KB given by `--db`);
  - project folder: `{"project": …}` in `lessons-kb.json`, shared by every project KB; otherwise `supplements/` inside each project KB directory, or `<name>.supplements/` beside a single-file project KB;
  - without project KBs (`--no-project`, or `--db` alone) there is one folder, the shared one, for every lesson read.
- **Which lessons:** `supplement --all` copies every lesson's evidence, each into its own folder. `--shared` does the shared lessons only, `--project` the project lessons only, and `supplement <lid> …` the lessons named.
- **What changes in the lessons:** each evidence entry with a copy gains `supplement` (its path inside its folder) and `sha256`. That is the only change, and it is allowed in normal sessions.
- **Every run recomputes the layout of the folders it runs on:** a copy moves when the broadest lesson citing it changes level. A copy that no lesson in that KB cites any more is deleted.
- **Copies in the wrong folder** (a lesson that moved between KBs, or a project lesson copied into the shared folder by an earlier version): the next `supplement --all` copies them into the lesson's own folder, from the original, or from the old copy if the original is gone. Project folders run first, and the shared folder keeps such a copy until the project folder has its own. `validate` warns while the shared folder holds copies that only project lessons cite.

### On request only
| Command | Writes | Purpose |
|---|---|---|
| `render --out FILE` | one digest of everything read | A single human-readable file |
| `condense … --out FILE` | one KB file | A small KB around one target system, to hand to an agent |
| `split --by profile\|… --out DIR` | `DIR/<shard>.json` | Shards for export |
| `merge A B … --out FILE` | one KB file; the old `FILE` goes to `backups/` beside it | Combining KBs, with a conflict report |

### Single-file KBs
When a KB is one JSON file (`--db kb.json`), its companions sit beside it: `kb.json.lock`, `kb.conflicts.json`, `backups/kb.json.<timestamp>`, and `kb.LESSONS.md` when it is a project KB.

## Files kbcli reads but never writes
| File | Read when | Notes |
|---|---|---|
| `profiles/<name>/profile.py`, `README.md`, `calibration.json`, `examples/`, `test_profile.py` | every command that loads profiles; `calibrate` | Built-in profiles ship in the package; the project's are listed under `profiles` in the config |
| Geometry files (`.xyz`) | `add-system` with `inputs.xyz`, `fingerprint --xyz`, `similar --xyz` | The fingerprint is stored in the system record; the file itself is not copied |
| Cited originals (`evidence[].path`) | `supplement` (to copy); `validate` (to notice changes since copying) | The original path stays the primary reference |

## Files you write yourself (conventions, no command)
| File | Convention | Purpose |
|---|---|---|
| `seed/<what>_<YYYYMMDD>.py` | GUIDE §10 | Seed and correction scripts, kept for provenance. Not rerun once applied |
| `kbcli.py` launcher | README, "Install" | Runs the framework from a submodule checkout |
| `.gitignore` entries | below | Keeps project and machine-local files out of version control |

A `.gitignore` for a project that commits only the shared KB:
```
db_project/
**/.lock
**/backups/
**/*.tmp[0-9]*
```

## By use case
| You are… | Files written |
|---|---|
| Starting a KB | `lessons-kb.json`, `db/general.json`, `db/.lock`; with `--db db_project init`, the same in `db_project/` |
| Reading (`similar`, `lessons`, `show`, `related`, `links`, `conflicts`, `validate`) | nothing |
| Adding a system, model or lesson | the shard(s) it lands in (routed to the shared or project KB), a backup of each shard rewritten, the `.lock` |
| Recording a conflict | `conflicts.json` in the KB it is routed to |
| Copying evidence | the shared and/or project `supplements/` folders (copies and `MANIFEST.json`), and the `supplement`/`sha256` links in the cited lessons' shards |
| Reconciling | the shards of the lessons changed, and `conflicts.json` (resolutions and archived lessons; the shared one too when a project-KB conflict removes a shared lesson) |
| Producing the digest | `LESSONS.md` beside the shared KB, and one inside each project KB directory |
| Handing a focused KB to an agent | the `condense --out` file |
| Exporting or bringing in another KB | `split --out` shards; `merge --out` file. To read another group's KB without merging, list it as a project KB; nothing is written to it unless records route there |

## Known rough edges
These follow from the current design, for discussion before any change:
1. **The shared digest is written even when the shared KB is empty.** A project that keeps everything in its project KB gets an empty top-level `LESSONS.md`.
2. **`MANIFEST.json`'s `base` is fixed at each folder's first run.** It is the common directory of the files cited then. Files named in documents outside it are never copied as referenced files.
3. **Copying is a separate step.** Nothing is copied when a lesson is added; run `supplement` after writing (GUIDE §11).
