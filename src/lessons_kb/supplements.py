'''Supplements: copies of the files a KB's lessons cite, stored beside the KB so its evidence travels with it.

Each kind of KB has its own supplements folder (GUIDE.md §2, docs/FILES.md): the shared KB's sits beside it, and
each project KB's inside it (or as set in lessons-kb.json), so project evidence stays with the project KB. A lesson's
copies are in the folder of the KB it is stored in. Inside a folder, the layout follows the level a lesson applies at:
  supplements/MANIFEST.json            {"base": <only files under it are copied>, "files": {rel: entry}}
  supplements/general/                 files cited by general lessons
  supplements/profile/<profile>/       ... by profile lessons
  supplements/family/<tag>/            ... by family lessons (their first tag, ':' -> '_')
  supplements/system/<system alias>/   ... by one-system lessons
  supplements/platform/<site>/         ... by lessons with a platform condition
  <level>/refs/                        files that documents in <level>/ name (scripts, tables): kind "referenced"
A file cited at several levels is stored once, at the broadest (general > profile > family > system > platform).
Names are the file's own; equal names from different sources get the parent folder as a prefix
(<parent>__<name>). Excerpts of large outputs are <stem>.L<a>-<b><ext>.

A cited copy is linked from its evidence entry: {"path": <original>, "note": ..., "supplement": <rel>,
"sha256": <hash of the copy>}. Every run recomputes the whole layout, moving copies that are still current and
copying the rest. Evidence entries are not part of a lesson's id, so linking copies never changes ids.'''
import datetime
import hashlib
import json
import os
import re
import shutil

MANIFEST = 'MANIFEST.json'
DEFAULT_MAX = 1000000             # bytes; larger files are skipped (store an excerpt instead)
LEVEL_RANK = {'general': 0, 'profile': 1, 'family': 2, 'system': 3, 'platform': 4}
_REF = re.compile(r'`([^`\s]+\.(?:xyz|py|sh|sbatch|job|csv|json|md|txt|log|out|aniso|inp|input))`'
                  r'|((?:/[\w.-]+)+/[\w.-]+\.(?:xyz|py|sh|sbatch|job|csv|json|md|txt|log|out|aniso|inp|input))')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def supplements_dir(kb_path):
    '''supplements/ beside a KB directory or file'''
    return os.path.join(os.path.dirname(os.path.abspath(kb_path).rstrip('/')), 'supplements')


def load_manifest(root):
    p = os.path.join(root, MANIFEST)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {'base': None, 'files': {}}


def save_manifest(root, man):
    os.makedirs(root, exist_ok=True)
    man['files'] = dict(sorted(man['files'].items()))
    tmp = os.path.join(root, MANIFEST + f'.tmp{os.getpid()}')
    with open(tmp, 'w') as f:
        f.write(json.dumps(man, indent=1, ensure_ascii=False) + '\n')
    os.replace(tmp, os.path.join(root, MANIFEST))


def _safe(name):
    return re.sub(r'_+', '_', re.sub(r'[^A-Za-z0-9_.+-]', '_', str(name))).strip('_') or '_'


def level_of(lesson, systems):
    '''(rank, folder) of the level a lesson applies at'''
    at = lesson.get('applies_to') or {}
    if at.get('platform'):
        return LEVEL_RANK['platform'], 'platform/' + _safe(sorted(at['platform'])[0].split(':', 1)[0])
    sc = lesson.get('scope')
    if sc == 'general':
        return LEVEL_RANK['general'], 'general'
    if sc == 'profile':
        return LEVEL_RANK['profile'], 'profile/' + _safe((sorted(at.get('profiles') or []) or ['any'])[0])
    if sc == 'family':
        return LEVEL_RANK['family'], 'family/' + _safe((sorted(at.get('tags') or []) or ['any'])[0])
    sid = (sorted(at.get('systems') or []) or ['unknown'])[0]
    return LEVEL_RANK['system'], 'system/' + _safe((systems.get(sid) or {}).get('alias', sid))


def referenced_files(doc, base):
    '''existing files under base that a text document names (in backticks, or as absolute paths), resolved
    against the document's directory and base'''
    out = []
    with open(doc, errors='replace') as f:
        text = f.read()
    here = os.path.dirname(doc)
    for m in _REF.finditer(text):
        ref = m.group(1) or m.group(2)
        for cand in ((ref,) if ref.startswith('/') else (os.path.join(here, ref), os.path.join(base, ref))):
            if os.path.isfile(cand):
                cand = os.path.abspath(cand)
                if not cand.startswith(base.rstrip('/') + '/'):     # never copy files from outside the base
                    break
                if cand not in out and cand != os.path.abspath(doc):
                    out.append(cand)
                break
    return out


def _name(src, lines):
    b = os.path.basename(src)
    if lines:
        stem, ext = os.path.splitext(b)
        b = f'{stem}.L{lines[0]}-{lines[1]}{ext}'
    return b


def sync(kb, root, lids=(), max_size=DEFAULT_MAX, base=None, referenced=False, excerpt=None, only=None, keep=(),
         fallback=()):
    '''copy the evidence of lessons lids (and keep every lesson already linked) into root, in the level layout;
    excerpt=(lid, path, (a, b)) stores lines a-b of one cited file instead of the whole file. only: the lessons
    this folder holds (default: all); linked lessons outside it are left alone. keep: copies (paths inside root)
    not to delete even if no lesson here cites them. fallback: other folders; when an original is gone, a lesson's
    copy there is copied here instead. Returns a report.'''
    man = load_manifest(root)
    old = man['files']
    if not man.get('base'):
        paths = [e['path'] for i in lids for e in kb.lessons[i]['evidence'] if os.path.isabs(e['path'])]
        base = base or (os.path.commonpath(paths) if paths else '/')
        man['base'] = os.path.dirname(base) if os.path.isfile(base) else base     # one cited file: its directory
    base = man['base']
    referenced = referenced or any(v.get('kind') == 'referenced' for v in old.values())
    old_by_key = {(v['source'], tuple(v['lines']) if v.get('lines') else None): (rel, v) for rel, v in old.items()}
    rep = {'copied': [], 'moved': [], 'kept': [], 'removed': [], 'linked': [], 'unlinked': [],
           'skipped_large': [], 'missing': [], 'referenced': []}
    todo = set(lids) | {i for i, l in kb.lessons.items() if any(e.get('supplement') for e in l.get('evidence', []))
                        and (only is None or i in only)}
    plan = {}                              # key -> {'levels': set((rank, dir)), 'cited_by', 'kind', ...}
    cite = {}                              # (lid, evidence index) -> key
    copy_from = {}                         # key -> file to copy when the original is gone (a copy in a fallback)
    fb = [(fr, load_manifest(fr)['files']) for fr in fallback if os.path.isdir(fr)]
    for lid in sorted(todo):
        l = kb.lessons[lid]
        lvl = level_of(l, kb.systems)
        for k, e in enumerate(l['evidence']):
            src = e['path']
            lines = None
            if excerpt and excerpt[0] == lid and os.path.abspath(excerpt[1]) == os.path.abspath(src):
                lines = tuple(excerpt[2])
            elif e.get('supplement') in old and old[e['supplement']].get('lines'):
                lines = tuple(old[e['supplement']]['lines'])
            if not os.path.isfile(src):
                if e.get('supplement') and e['supplement'] in old:      # original gone: keep the copy we have
                    lines = tuple(old[e['supplement']]['lines']) if old[e['supplement']].get('lines') else None
                    src = old[e['supplement']]['source']
                else:                                                   # or take the copy in another folder
                    hit = next(((fr, fm[e['supplement']]) for fr, fm in fb if e.get('supplement') in fm
                                and os.path.isfile(os.path.join(fr, e['supplement']))), None)
                    if hit is None:
                        rep['missing'].append((lid, src)); continue
                    lines = tuple(hit[1]['lines']) if hit[1].get('lines') else None
                    src = hit[1]['source']
                    copy_from[(os.path.abspath(src), lines)] = os.path.join(hit[0], e['supplement'])
            elif not lines and os.path.getsize(src) > max_size:
                rep['skipped_large'].append((lid, src, os.path.getsize(src))); continue
            key = (os.path.abspath(src), lines)
            p = plan.setdefault(key, {'levels': set(), 'cited_by': set(), 'referenced_by': set(),
                                      'kind': 'excerpt' if lines else 'cited'})
            p['levels'].add(lvl); p['cited_by'].add(lid)
            cite[(lid, k)] = key
    if referenced:
        for (src, lines), p in list(plan.items()):
            if lines or not src.endswith(('.md', '.txt')) or not os.path.isfile(src):
                continue
            for ref in referenced_files(src, base):
                if os.path.getsize(ref) > max_size:
                    rep['skipped_large'].append((f'(named in {os.path.basename(src)})', ref, os.path.getsize(ref)))
                    continue
                q = plan.setdefault((ref, None), {'levels': set(), 'cited_by': set(), 'referenced_by': set(),
                                                  'kind': 'referenced', 'ref_levels': set()})
                q.setdefault('ref_levels', set()).update(p['levels']); q['referenced_by'].add(src)
                rep['referenced'].append(ref)
    # where each file goes: the broadest level that cites it (or, for referenced files, that names it)
    dest = {}
    for key, p in plan.items():
        if p['levels']:
            folder = min(p['levels'])[1]
        else:
            folder = min(p['ref_levels'])[1] + '/refs'
        dest[key] = folder
    names = {}
    for key in sorted(plan):
        names.setdefault((dest[key], _name(*key)), []).append(key)
    rel = {}
    for (folder, name), keys in names.items():
        for key in keys:
            n = name if len(keys) == 1 else f'{_safe(os.path.basename(os.path.dirname(key[0])))}__{name}'
            rel[key] = f'{folder}/{n}'
        if len({rel[k] for k in keys}) < len(keys):           # same parent folder name too: add a short hash
            for key in keys:
                h = hashlib.sha256(key[0].encode()).hexdigest()[:8]
                rel[key] = f'{folder}/{h}__{name}'
    # carry out: reuse current copies (moved through a staging folder), copy the rest, drop stale ones
    stage = os.path.join(root, f'.staging{os.getpid()}')
    staged = {}
    today = datetime.date.today().isoformat()
    for key in plan:
        hit = old_by_key.get(key)
        if not hit:
            continue
        orel, ov = hit
        f = os.path.join(root, orel)
        src_ok = key[1] is not None or not os.path.isfile(key[0]) or sha256(key[0]) == ov.get('sha256_source')
        if os.path.isfile(f) and sha256(f) == ov['sha256'] and src_ok:
            os.makedirs(stage, exist_ok=True)
            tmp = os.path.join(stage, str(len(staged)))
            os.rename(f, tmp)
            staged[key] = (tmp, orel, ov)
    files = {}
    for key, p in plan.items():
        r = rel[key]
        dst = os.path.join(root, r)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if key in staged:
            tmp, orel, ov = staged[key]
            os.rename(tmp, dst)
            (rep['kept'] if orel == r else rep['moved']).append(r)
            copied, h, hs = ov.get('copied', today), ov['sha256'], ov.get('sha256_source')
        else:
            src, lines = key
            if key in copy_from:                                    # the original is gone: copy another folder's copy
                shutil.copy2(copy_from[key], dst)
            elif lines:
                with open(src, errors='replace') as fh:
                    part = [x for i, x in enumerate(fh, 1) if lines[0] <= i <= lines[1]]
                with open(dst, 'w') as fh:
                    fh.write(f'# excerpt of {src}, lines {lines[0]}-{lines[1]}\n' + ''.join(part))
            else:
                shutil.copy2(src, dst)
            copied, h = today, sha256(dst)
            hs = sha256(src) if os.path.isfile(src) else None
            rep['copied'].append(r)
        ent = {'source': key[0], 'kind': p['kind'], 'sha256': h, 'sha256_source': hs,
               'size': os.path.getsize(dst), 'copied': copied, 'cited_by': sorted(p['cited_by'])}
        if key[1]:
            ent['lines'] = list(key[1])
        if p['referenced_by']:
            ent['referenced_by'] = sorted(p['referenced_by'])
        files[r] = ent
    for orel in old:
        f = os.path.join(root, orel)
        if orel in keep and orel not in files and os.path.isfile(f):
            files[orel] = old[orel]; rep['kept'].append(orel)
        elif orel not in files and os.path.isfile(f):
            os.remove(f); rep['removed'].append(orel)
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    for dirpath, dirnames, filenames in sorted(os.walk(root), key=lambda x: -len(x[0])):
        if dirpath != root and not os.listdir(dirpath):
            os.rmdir(dirpath)
    # links in the lessons
    now = datetime.datetime.now().isoformat(timespec='seconds')
    for lid in sorted(todo):
        l = kb.lessons[lid]
        changed = False
        for k, e in enumerate(l['evidence']):
            key = cite.get((lid, k))
            if key is None:
                if e.pop('supplement', None) is not None:
                    e.pop('sha256', None); changed = True; rep['unlinked'].append(lid)
                continue
            r = rel[key]
            if e.get('supplement') != r or e.get('sha256') != files[r]['sha256']:
                e['supplement'], e['sha256'] = r, files[r]['sha256']
                changed = True; rep['linked'].append((lid, r))
        if changed:
            l['updated'] = now
    man['files'] = files
    save_manifest(root, man)
    return rep


def project_dir(kb_path):
    '''the default supplements folder of a project KB: supplements/ inside its directory, or <name>.supplements/
    beside a single-file KB'''
    kb_path = os.path.abspath(kb_path).rstrip('/')
    if kb_path.endswith('.json') and not os.path.isdir(kb_path):
        return kb_path[:-5] + '.supplements'
    return os.path.join(kb_path, 'supplements')


def _same_source(a, b):
    return a.get('source') == b.get('source') and a.get('lines') == b.get('lines')


def foreign_copies(root, ids, owner):
    '''copies in root that cite only lessons stored elsewhere, while those lessons' own folder has no copy yet
    (their links still point here): sync must keep them until the lessons are copied into their own folder'''
    keep = set()
    man = load_manifest(root) if os.path.isdir(root) else {'files': {}}
    others = {}
    for rel, ent in man['files'].items():
        for lid in ent.get('cited_by', []):
            own = owner.get(lid)
            if lid in ids or own is None or own == root:
                continue
            if own not in others:
                others[own] = list(load_manifest(own)['files'].values()) if os.path.isdir(own) else []
            if not any(_same_source(ent, v) for v in others[own]):
                keep.add(rel)
    return keep


def sync_roots(kb, roots, lids=None, run=None, **kw):
    '''sync several folders. roots: [(root, ids of the lessons stored in its KB)] for every folder, project folders
    first, so a lesson's copy is in its own folder before the shared folder lets go of it. run: the folders to sync
    (default: all). lids: the lessons to copy (None: every lesson of each folder run). Returns {root: report}.'''
    owner = {i: r for r, ids in roots for i in ids}
    reps = {}
    for root, ids in roots:
        if run is not None and root not in run:
            continue
        ids = set(ids)
        want = sorted(ids if lids is None else ids & set(lids))
        keep = foreign_copies(root, ids, owner)
        if not want and not keep and not os.path.isdir(root):
            continue
        reps[root] = sync(kb, root, want, only=ids, keep=keep, fallback=[r for r, _ in roots if r != root], **kw)
    return reps


def check(kb, root, only=None):
    '''(errors, warnings) for the supplements of kb's lessons (only: the lessons stored with this folder)'''
    E, W = [], []
    lessons = {i: l for i, l in kb.lessons.items() if only is None or i in only}
    if not root or not os.path.isdir(root):
        if any(e.get('supplement') for l in lessons.values() for e in l.get('evidence', [])):
            W.append(f'lessons link supplements, but {root} does not exist; rerun `kbcli supplement`')
        return E, W
    man = load_manifest(root)
    for lid, l in lessons.items():
        for e in l.get('evidence', []):
            rel = e.get('supplement')
            if not rel:
                continue
            p = os.path.join(root, rel)
            if not os.path.isfile(p):
                W.append(f'lesson {lid}: supplement {rel} is missing'); continue
            if e.get('sha256') and sha256(p) != e['sha256']:
                W.append(f'lesson {lid}: supplement {rel} does not match its checksum')
            ent = man['files'].get(rel)
            if (ent and ent.get('kind') != 'excerpt' and ent.get('sha256_source') and os.path.isfile(e['path'])
                    and sha256(e['path']) != ent['sha256_source']):
                W.append(f'lesson {lid}: {e["path"]} changed since it was copied; rerun `kbcli.py supplement {lid}`')
    for rel in man['files']:
        if not os.path.isfile(os.path.join(root, rel)):
            W.append(f'supplements manifest lists {rel}, which is missing')
    return E, W


def check_roots(kb, roots, shared_root=None):
    '''check() for each (root, ids); also warns when the shared folder holds copies that only project lessons cite'''
    E, W = [], []
    owner = {i: r for r, ids in roots for i in ids}
    for root, ids in roots:
        e, w = check(kb, root, set(ids)); E += e; W += w
    if shared_root and os.path.isdir(shared_root):
        for rel, ent in load_manifest(shared_root)['files'].items():
            by = [i for i in ent.get('cited_by', []) if i in kb.lessons]
            if by and all(owner.get(i) not in (None, shared_root) for i in by):
                W.append(f'shared supplements hold {rel}, cited only by project lessons ({", ".join(by)}); run '
                         '`kbcli supplement --all` to move it to the project folder')
    return E, W
