'''Lessons-learned knowledge base: domain-agnostic core.

Data model (see GUIDE.md for the field rules):
  {"kb_meta": {...}, "systems": {sys_id: system}, "lessons": {lesson_id: lesson}}
System ids come from the system's profile (sha256 of its canonical identity key); lesson ids are sha256 of
category|title|text|date. Both are content-derived, so the same record has the same id in every shard and
shards can be split and merged safely.'''
import contextlib
import copy
import datetime
import fcntl
import glob
import hashlib
import json
import os
import re

from .profile import Profile, load_profiles, sha12

SCHEMA_VERSION = 1
CORE_CATEGORIES = {
    'model_definition': 'what to include in the model and how (regions, boundaries, what is treated how)',
    'method': 'choice and behaviour of the level of theory',
    'basis': 'basis sets and auxiliary bases',
    'active_space': 'active-space / reference selection and its pitfalls',
    'environment': 'embedding, solvent, periodic or other surroundings',
    'geometry': 'structures, relaxation, conformers',
    'convergence': 'SCF/CASSCF/optimizer convergence behaviour and fixes',
    'analysis': 'how to extract, fit, compare or report results',
    'reference_data': 'experimental or benchmark references and their caveats',
    'resources': 'memory, time, node and storage sizing',
    'software': 'code bugs, quirks and workarounds',
    'workflow': 'job orchestration, checkpoints, documentation practice',
}
SCOPES = ('general', 'profile', 'family', 'system')
# setup conditions: orthogonal to scope (which chemistry); say which software/platform a lesson depends on
SETUP_KEYS = ('software', 'platform')
APPLIES_KEYS = ('profiles', 'systems', 'tags') + SETUP_KEYS
CONFIDENCE = ('established', 'provisional', 'open_question')
SOURCES = ('computed', 'user_decision', 'literature')
LESSON_STATUS = ('active', 'superseded')
REVIEW_LEVELS = ('full', 'stub')
MODEL_STATUS = ('planned', 'pending', 'running', 'done', 'failed', 'abandoned')
LESSON_TEXT_WARN = 600          # characters; longer texts usually hold more than one statement (GUIDE §4 rule 3)
SYSTEM_KEYS = ('id', 'profile', 'profile_version', 'alias', 'canonical', 'identity', 'fingerprint',
               'fingerprint_inputs', 'tags', 'reference_data', 'models', 'best_model', 'open_questions',
               'user_decisions', 'docs', 'review', 'notes', 'created', 'updated')
MODEL_KEYS = ('id', 'status', 'dir', 'level_of_theory', 'model_details', 'purpose', 'jobs', 'result_summary',
              'docs', 'notes', 'date', 'updated')
LESSON_KEYS = ('id', 'title', 'text', 'category', 'scope', 'applies_to', 'evidence', 'confidence', 'source',
               'status', 'superseded_by', 'date', 'updated', 'author', 'seeded_from_docs', 'reviewed', 'tags',
               'context', 'references', 'referenced_by', 'coverage', 'verification')
# an evidence entry: path (required, never changed) and note, plus optional keys that make the claim checkable
# (GUIDE §3); supplement and sha256 are written by `kbcli supplement`
EVIDENCE_KEYS = ('path', 'note', 'kind', 'lines', 'quote', 'supports', 'model', 'supplement', 'sha256')
EVIDENCE_KINDS = ('output', 'input', 'analysis', 'note', 'literature')
# a verification record: one check of a lesson's claims against its evidence (appended, newest last)
VERIFICATION_KEYS = ('date', 'by', 'method', 'verdict', 'note')
VERIFY_METHODS = ('agent', 'person')
VERDICTS = ('supported', 'partly', 'not_supported', 'cannot_verify')
# metadata: lesson fields that say how well the claim is supported, not what it claims or where it applies.
# Normal sessions may edit them; everything else changes only in reconciliation sessions (GUIDE §1, §12)
LESSON_METADATA = ('evidence', 'coverage', 'verification', 'reviewed')
# universal wording that needs coverage (GUIDE §4 rule 9); hyphenated compounds such as all-electron don't count
UNIVERSAL_WORDS = re.compile(r'(?<![\w\-‐‑])(every|all|always|never|must)(?![\w\-‐‑])', re.I)
# links between lessons (GUIDE §3): a lesson's own `references` say how it relates to others; `referenced_by` is
# derived on every load and save from references, superseded_by and conflicts, never written by hand
REF_RELS = ('builds_on', 'supports', 'qualifies', 'see_also')
DERIVED_RELS = ('superseded', 'contested', 'reconciled')
# structured context of a lesson or conflict: what reconciliation compares (GUIDE §3, §12)
CONTEXT_KEYS = ('systems', 'observables', 'methods', 'conditions')
# modes: 'general' (normal sessions: the KB is append-only) and 'reconcile' (dedicated reconciliation sessions)
MODES = ('general', 'reconcile')
GENERAL_REFUSAL = ('existing records change only in reconciliation sessions (GUIDE §12); if a new finding '
                   'contradicts the KB, record it with `kbcli.py conflict add`')
CONFLICTS_FILE = 'conflicts.json'
CONFLICT_STATUS = ('open', 'resolved', 'rejected')
RESOLUTION_ACTIONS = ('replace', 'constrain', 'relevel', 'merge', 'split', 'reject', 'both_hold', 'remove', 'relink')
# fields that define a lesson (part of its id): change them by superseding, not editing
LESSON_IMMUTABLE = ('id', 'title', 'text', 'category', 'date', 'author')
N_BACKUPS = 20


class KBError(Exception):
    pass


def now():
    return datetime.datetime.now().isoformat(timespec='seconds')


def today():
    return datetime.date.today().isoformat()


def lesson_id(category, title, text, date):
    return 'L-' + hashlib.sha256(f'{category}|{title}|{text}|{date}'.encode()).hexdigest()[:10]


# ---------------------------------------------------------------- record builders
def build_system(profile, identity, inputs=None, partial=False, **extra):
    '''Make a system record. extra: any other SYSTEM_KEYS (tags are merged with profile.suggest_tags).
    partial=True builds a similarity query (missing identity fields allowed, id None).'''
    if isinstance(profile, str):
        profile = load_profiles()[profile]
    bad = set(extra) - set(SYSTEM_KEYS)
    if bad:
        raise KBError(f'unknown system field(s) {sorted(bad)}')
    ident = profile.normalize_identity(identity, partial=partial)
    inputs = dict(inputs or {})
    fp = profile.fingerprint(ident, inputs)
    t = now()
    rec = {'id': None if partial else profile.system_id(ident), 'profile': profile.name,
           'profile_version': profile.version, 'alias': profile.alias(ident), 'canonical': profile.canonical(ident),
           'identity': ident, 'fingerprint': fp, 'fingerprint_inputs': inputs, 'tags': [], 'reference_data': {},
           'models': [], 'best_model': None, 'open_questions': [], 'user_decisions': [], 'docs': [],
           'review': {'level': 'stub', 'date': today()}, 'notes': '', 'created': t, 'updated': t}
    for k, v in extra.items():
        if k not in ('id', 'canonical', 'profile', 'profile_version', 'fingerprint'):
            rec[k] = copy.deepcopy(v)
    rec['tags'] = sorted(set(rec['tags']) | set(profile.suggest_tags(ident, fp)))
    return rec


def norm_setup(v):
    '''setup values are lower-case, space-free tokens, optionally "name:detail" (e.g. "pyscf", "openmolcas:podman")'''
    return '-'.join(str(v).strip().lower().split())


def setup_match(lesson, setup=None):
    '''True if the lesson has no setup condition or the declared setup meets it, False if a declared key
    conflicts, None if the lesson is conditional on a key the caller did not declare. Within a key any value
    matches; across keys all must. "x" and "x:detail" match each other.'''
    at = lesson.get('applies_to') or {}
    setup = setup or {}
    undeclared = False
    for k in SETUP_KEYS:
        want = at.get(k) or []
        if not want:
            continue
        have = [norm_setup(v) for v in setup.get(k) or []]
        if not have:
            undeclared = True
        elif not any(w == h or h.startswith(w + ':') or w.startswith(h + ':') for w in want for h in have):
            return False
    return None if undeclared else True


def norm_context(c):
    '''{key: [str, ...]} with keys from CONTEXT_KEYS; raises KBError on anything else'''
    if c is None:
        return None
    if not isinstance(c, dict) or set(c) - set(CONTEXT_KEYS):
        raise KBError(f'context must be a dict with keys from {CONTEXT_KEYS}')
    out = {}
    for k in CONTEXT_KEYS:
        v = c.get(k) or []
        v = [v] if isinstance(v, str) else v
        if not all(isinstance(x, str) and x.strip() for x in v):
            raise KBError(f'context.{k} must be a list of non-empty strings')
        out[k] = [x.strip() for x in v]
    return out


def _str_list(v, what):
    v = [v] if isinstance(v, str) else (v or [])
    if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
        raise KBError(f'{what} must be a list of non-empty strings')
    return [x.strip() for x in v]


def norm_lines(v):
    '''"A-B" (or "A") from "A-B", "A", an int or [A, B]; raises KBError on anything else'''
    if isinstance(v, int) and not isinstance(v, bool):
        v = [v, v]
    elif isinstance(v, str) and re.fullmatch(r'\s*\d+\s*(-\s*\d+\s*)?', v):
        v = [int(x) for x in v.split('-')] * (1 if '-' in v else 2)
    if not (isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, int) and x > 0 for x in v)
            and v[0] <= v[1]):
        raise KBError(f'evidence lines must be "A-B" with 0 < A <= B, not {v!r}')
    return f'{v[0]}' if v[0] == v[1] else f'{v[0]}-{v[1]}'


def norm_evidence_entry(e):
    '''an evidence entry with its optional keys checked (GUIDE §3); unknown keys are kept (validate warns)'''
    if not isinstance(e, dict) or not isinstance(e.get('path'), str) or not e['path'].strip():
        raise KBError(f'evidence entries are {{path, note, ...}}, not {e!r}')
    e = dict(e)
    if e.get('kind') is not None and e['kind'] not in EVIDENCE_KINDS:
        raise KBError(f"evidence kind must be one of {EVIDENCE_KINDS}, not {e['kind']!r}")
    if e.get('lines') is not None:
        e['lines'] = norm_lines(e['lines'])
    for k in ('note', 'quote', 'supports', 'model'):
        if e.get(k) is not None and not isinstance(e[k], str):
            raise KBError(f'evidence {k} must be a string')
    return e


def norm_verification(v):
    '''[{date, by, method, verdict, note?}] (one record per check, newest last); raises KBError on anything else'''
    out = []
    for r in [v] if isinstance(v, dict) else (v or []):
        if not isinstance(r, dict) or set(r) - set(VERIFICATION_KEYS):
            raise KBError(f'verification records are {{{", ".join(VERIFICATION_KEYS)}}}, not {r!r}')
        r = dict(r)
        r.setdefault('date', today()); r.setdefault('by', ''); r.setdefault('method', 'agent')
        if r.get('verdict') not in VERDICTS:
            raise KBError(f"verification verdict must be one of {VERDICTS}, not {r.get('verdict')!r}")
        if r['method'] not in VERIFY_METHODS:
            raise KBError(f"verification method must be one of {VERIFY_METHODS}, not {r['method']!r}")
        out.append(r)
    return out


def universal_words(lesson):
    '''the universal words (every, all, always, never, must) in a lesson's title and text, lower-cased'''
    return sorted({m.group(1).lower() for m in UNIVERSAL_WORDS.finditer(f"{lesson.get('title', '')} "
                                                                           f"{lesson.get('text', '')}")})


def norm_references(refs):
    '''[{id, rel, note?}] from a list of dicts or bare ids (rel see_also); raises KBError on anything else'''
    out = []
    for r in refs or []:
        r = {'id': r} if isinstance(r, str) else dict(r)
        if not str(r.get('id', '')).startswith('L-') or set(r) - {'id', 'rel', 'note'}:
            raise KBError(f'references entries are {{id: L-..., rel, note?}}, not {r!r}')
        r.setdefault('rel', 'see_also')
        if r['rel'] not in REF_RELS:
            raise KBError(f"reference rel must be one of {REF_RELS}, not {r['rel']!r}")
        if not any(x['id'] == r['id'] and x['rel'] == r['rel'] for x in out):
            out.append(r)
    return out


def backlinks(lessons, conflicts=None):
    '''{lesson id: [{id, rel}]}: who points at each lesson. rel is the source's reference rel, "superseded" (that
    lesson is superseded by this one), or "contested"/"reconciled" (an open/closed conflict names this lesson)'''
    out = {}
    def add(target, src, rel):
        if target in lessons and target != src:
            e = {'id': src, 'rel': rel}
            if e not in out.setdefault(target, []):
                out[target].append(e)
    for lid, l in lessons.items():
        for r in l.get('references') or []:
            add(r['id'], lid, r['rel'])
        for x in successors(l):
            add(x, lid, 'superseded')
    for cid, c in (conflicts or {}).items():
        rel = 'contested' if c.get('status') == 'open' else 'reconciled'
        for x in list(c.get('contradicts', [])) + list((c.get('resolution') or {}).get('result_ids', [])):
            add(x, cid, rel)
    return {k: sorted(v, key=lambda e: (e['rel'], e['id'])) for k, v in out.items()}


def _apply_backlinks(lessons, conflicts=None):
    '''set (or drop) referenced_by on every lesson dict, in place'''
    bl = backlinks(lessons, conflicts)
    for lid, l in lessons.items():
        if bl.get(lid):
            l['referenced_by'] = bl[lid]
        else:
            l.pop('referenced_by', None)


def context_ok(c):
    return bool(c) and any(c.get(k) for k in CONTEXT_KEYS)


def _shard_files(d):
    '''the shard files of a KB directory (everything *.json except the conflicts file)'''
    return sorted(f for f in glob.glob(os.path.join(d, '*.json')) if os.path.basename(f) != CONFLICTS_FILE)


def conflicts_path(kb_path):
    '''<dir>/conflicts.json for a shard directory, <name>.conflicts.json beside a single KB file'''
    kb_path = os.path.abspath(kb_path)
    if os.path.isdir(kb_path) or not kb_path.endswith('.json'):
        return os.path.join(kb_path, CONFLICTS_FILE)
    return kb_path[:-5] + '.conflicts.json'


def _read_conflicts(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f).get('conflicts', {})
    return {}


def _write_conflicts(path, conflicts):
    '''write only when the content changes'''
    text = json.dumps({'schema_version': SCHEMA_VERSION, 'conflicts': dict(sorted(conflicts.items()))},
                      indent=1, ensure_ascii=False) + '\n'
    if os.path.exists(path):
        with open(path) as f:
            if f.read() == text:
                return
    elif not conflicts:
        return
    tmp = f'{path}.tmp{os.getpid()}'
    with open(tmp, 'w') as f:
        f.write(text); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def _empty(v):
    return v is None or v == '' or v == [] or v == {}


def _fill_ok(cur, new):
    '''general-mode rule for an existing field: fill it if empty, keep it if unchanged, or extend a list'''
    if _empty(cur) or cur == new:
        return True
    return isinstance(cur, list) and isinstance(new, list) and all(x in new for x in cur)


def make_lesson(d):
    '''fill defaults and the id of a lesson dict (title, text, category, scope, evidence, ... see GUIDE.md)'''
    bad = set(d) - set(LESSON_KEYS)
    if bad:
        raise KBError(f'unknown lesson field(s) {sorted(bad)}')
    l = {'applies_to': {}, 'confidence': 'provisional', 'source': 'computed', 'status': 'active',
         'superseded_by': None, 'date': today(), 'author': '', 'seeded_from_docs': False, 'reviewed': False,
         'tags': []}
    l.update(copy.deepcopy(d))
    bad = set(l['applies_to']) - set(APPLIES_KEYS)
    if bad:
        raise KBError(f'unknown applies_to key(s) {sorted(bad)}; allowed {APPLIES_KEYS}')
    l['applies_to'] = {k: list(l['applies_to'].get(k, [])) for k in APPLIES_KEYS}
    for k in SETUP_KEYS:
        l['applies_to'][k] = sorted({norm_setup(v) for v in l['applies_to'][k]})
    if 'context' in l:
        l['context'] = norm_context(l['context'])
    if 'references' in l:
        l['references'] = norm_references(l['references'])
    if isinstance(l.get('evidence'), list):
        l['evidence'] = [norm_evidence_entry(e) for e in l['evidence']]
    if 'coverage' in l:
        l['coverage'] = _str_list(l['coverage'], 'coverage')
    if 'verification' in l:
        l['verification'] = norm_verification(l['verification'])
    l.pop('referenced_by', None)                     # derived, never taken from input
    for k in ('title', 'text', 'category'):
        if not l.get(k):
            raise KBError(f'lesson needs a non-empty {k!r}')
    lid = lesson_id(l['category'], l['title'], l['text'], l['date'])
    if l.get('id') and l['id'] != lid:
        raise KBError(f"lesson id {l['id']} does not match its content ({lid}); supersede instead of editing")
    l['id'] = lid
    l.setdefault('updated', now())
    return l


def successors(lesson):
    '''ids that supersede a lesson: [] if active, one id, or several when it was split'''
    v = lesson.get('superseded_by')
    return [v] if isinstance(v, str) and v else list(v) if isinstance(v, (list, tuple)) else []


_STOP = set('''that this with from when than then they their there these those into only also more most less does were
have been which while where what each every other same both about after before over under between within without
above below should would could must done used uses using give gives given make makes made keep keeps kept stay stays
need needs like just even very much many such not but and for the are was its can all any one two per via'''.split())


def lesson_terms(lesson):
    '''content words of a lesson's title and text, for finding related lessons'''
    w = re.findall(r'[a-z][a-z0-9+\-]*', (lesson.get('title', '') + ' ' + lesson.get('text', '')).lower())
    return {x.strip('-') for x in w if len(x) >= 3 and x not in _STOP}


def _sentences(text):
    return [x.strip() for x in re.split(r'(?<=[.;])\s+', text) if len(x.strip()) >= 40]


# ---------------------------------------------------------------- merging helpers
def _merge_val(a, b, path, conflicts, prefer_b):
    if a == b:
        return copy.deepcopy(a)
    if a is None or a == '' or a == [] or a == {}:
        return copy.deepcopy(b)
    if b is None or b == '' or b == [] or b == {}:
        return copy.deepcopy(a)
    if isinstance(a, dict) and isinstance(b, dict):
        out = {}
        for k in list(a) + [k for k in b if k not in a]:
            if k in a and k in b:
                out[k] = _merge_field(k, a[k], b[k], path + [k], conflicts, prefer_b)
            else:
                out[k] = copy.deepcopy(a[k] if k in a else b[k])
        return out
    if isinstance(a, list) and isinstance(b, list):
        if all(isinstance(x, dict) and 'id' in x for x in a + b):
            out = [copy.deepcopy(x) for x in a]; pos = {x['id']: i for i, x in enumerate(out)}
            for y in b:
                if y['id'] in pos:
                    out[pos[y['id']]] = _merge_val(out[pos[y['id']]], y, path + [y['id']], conflicts, prefer_b)
                else:
                    pos[y['id']] = len(out); out.append(copy.deepcopy(y))
            return out
        out = copy.deepcopy(a); seen = [json.dumps(x, sort_keys=True) for x in a]
        for y in b:
            s = json.dumps(y, sort_keys=True)
            if s not in seen:
                out.append(copy.deepcopy(y)); seen.append(s)
        return out
    conflicts.append({'path': '/'.join(map(str, path)), 'a': a, 'b': b, 'kept': 'b' if prefer_b else 'a'})
    return copy.deepcopy(b if prefer_b else a)


def _merge_field(k, a, b, path, conflicts, prefer_b):
    if k == 'updated' and isinstance(a, str) and isinstance(b, str):
        return max(a, b)
    if k == 'created' and isinstance(a, str) and isinstance(b, str):
        return min(a, b)
    if k == 'status' and {a, b} == {'active', 'superseded'}:
        return 'superseded'                      # superseding is monotonic
    if k == 'reviewed' and isinstance(a, bool) and isinstance(b, bool):
        return a or b
    if k == 'superseded_by' and a and b:
        ids = successors({k: a}) + [x for x in successors({k: b}) if x not in successors({k: a})]
        return ids[0] if len(ids) == 1 else ids
    return _merge_val(a, b, path, conflicts, prefer_b)


def _prefer_b(a, b, prefer):
    if prefer == 'first':
        return False
    if prefer == 'last':
        return True
    return b.get('updated', '') > a.get('updated', '')   # newer


def _safe(name):
    return re.sub(r'[^A-Za-z0-9_.+-]', '_', str(name)) or '_'


# ---------------------------------------------------------------- the KB
GENERAL_SHARD = 'general'


def shard_of_lesson(lesson, systems):
    '''shard file (without .json) a lesson is stored in when the KB is a directory:
    platform_<base> if it has a platform condition with one base name (site knowledge; drop these shards when
    exporting elsewhere), else the single profile it concerns (scope profile/family/system), else general.'''
    at = lesson.get('applies_to') or {}
    bases = {p.split(':', 1)[0] for p in at.get('platform', [])}
    if len(bases) == 1:
        return _safe(f'platform_{bases.pop()}')
    if lesson.get('scope') == 'general':
        return GENERAL_SHARD
    profs = set(at.get('profiles', [])) | {systems[i]['profile'] for i in at.get('systems', []) if i in systems}
    return _safe(profs.pop()) if len(profs) == 1 else GENERAL_SHARD


def shard_of_system(system):
    return _safe(system['profile'])


# ---------------------------------------------------------------- shared and project KBs
# A KB can be read from several sources at once: one *shared* KB (portable knowledge, fit to publish) and any
# number of *project* KBs (the systems studied, decisions and site knowledge of one group). Reads merge them;
# every record is written back to the source it came from; new records are routed by route_new().
SHARED, PROJECT = 'shared', 'project'
ROLES = (SHARED, PROJECT)


def route_new(kind, rec):
    '''role a new record belongs to: systems (and their models), one-system lessons, user decisions and site
    (platform) lessons are project knowledge; everything else is shared. Override with KB.place().'''
    if kind == 'systems':
        return PROJECT
    at = rec.get('applies_to') or {}
    if rec.get('scope') == 'system' or at.get('systems') or at.get('platform') or rec.get('source') == 'user_decision':
        return PROJECT
    return SHARED


def _home_dirs():
    h = os.path.expanduser('~')
    return {d.rstrip('/') + '/' for d in (h, os.path.realpath(h)) if d and d != '~'}


class KB:
    def __init__(self, data=None, path=None, profiles=None):
        self.data = data or {'kb_meta': {'schema_version': SCHEMA_VERSION, 'created': now(), 'updated': now(),
                                         'profiles': {}, 'origin': []},
                             'systems': {}, 'lessons': {}}
        self.data.setdefault('systems', {}); self.data.setdefault('lessons', {})
        self.path = path
        self.profiles = profiles if profiles is not None else load_profiles()
        self.sources = None          # [(role, path)] when loaded from several sources (load_sources)
        self.origin = {}             # (kind, id) -> set of source paths the record was read from
        self.placement = {}          # (kind, id) -> role, set by place(); overrides origin and route_new
        self.mode = None             # set by transaction(): 'general' guards existing records (GUIDE §12)
        self.conflicts = {}          # C-id -> conflict record (conflicts.json beside the shards)
        self.conflict_origin = {}    # C-id -> source path it was read from

    def _guard(self, what):
        if self.mode == 'general':
            raise KBError(f'{what} is not allowed outside reconciliation: {GENERAL_REFUSAL}')

    systems = property(lambda self: self.data['systems'])
    lessons = property(lambda self: self.data['lessons'])
    meta = property(lambda self: self.data['kb_meta'])

    # ------------------------------------------------------------ io
    @classmethod
    def load(cls, path, profiles=None):
        '''path: a KB json file, or a directory of shard files (merged in memory, read-only)'''
        if os.path.isdir(path):
            files = _shard_files(path)
            if not files:
                raise KBError(f'no *.json shards in {path}')
            kb, _ = cls.merge([cls.load(f, profiles) for f in files], prefer='newer', profiles=profiles)
            kb.path = None
            kb.meta['origin'] = [{'shards': [os.path.basename(f) for f in files]}]
            kb.conflicts = _read_conflicts(conflicts_path(path))
            _apply_backlinks(kb.lessons, kb.conflicts)
            return kb
        if not os.path.exists(path):
            raise KBError(f'{path} does not exist (create it with `kbcli.py init`)')
        with open(path) as f:
            data = json.load(f)
        if data.get('kb_meta', {}).get('schema_version') != SCHEMA_VERSION:
            raise KBError(f'{path}: schema_version {data.get("kb_meta", {}).get("schema_version")} != {SCHEMA_VERSION}')
        kb = cls(data, path, profiles)
        kb.conflicts = _read_conflicts(conflicts_path(path))
        _apply_backlinks(kb.lessons, kb.conflicts)
        return kb

    def save(self, path=None, backup=True):
        path = path or self.path
        if not path:
            raise KBError('this KB was loaded from a shard directory; write it with save_shards() or transaction()')
        self.meta['updated'] = now()
        self.meta['profiles'] = {s['profile']: self.profiles[s['profile']].version
                                 for s in self.systems.values() if s['profile'] in self.profiles}
        self.data['systems'] = dict(sorted(self.systems.items()))
        self.data['lessons'] = dict(sorted(self.lessons.items()))
        lessons = copy.deepcopy(self.lessons); _apply_backlinks(lessons, self.conflicts)
        text = json.dumps(dict(self.data, lessons=dict(sorted(lessons.items()))), indent=1, ensure_ascii=False) + '\n'
        if backup and os.path.exists(path):
            self._backup(path)
        tmp = f'{path}.tmp{os.getpid()}'
        with open(tmp, 'w') as f:
            f.write(text); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
        self.path = path
        _write_conflicts(conflicts_path(path), self.conflicts)

    def save_shards(self, dirpath, backup=True):
        '''write this KB as a shard directory (routing: shard_of_system / shard_of_lesson). Only shards whose
        records change are rewritten; shards that end up empty are removed (general.json always stays).
        New and growing shards are written before shrinking ones, so an interruption can duplicate a record
        (merged away on load) but never lose one. Call under the directory lock (transaction).'''
        os.makedirs(dirpath, exist_ok=True)
        want = {GENERAL_SHARD: ({}, {})}
        for i, rec in self.systems.items():
            want.setdefault(shard_of_system(rec), ({}, {}))[0][i] = rec
        lessons = copy.deepcopy(self.lessons); _apply_backlinks(lessons, self.conflicts)
        for i, rec in lessons.items():
            want.setdefault(shard_of_lesson(rec, self.systems), ({}, {}))[1][i] = rec
        existing = {os.path.basename(f)[:-5]: f for f in _shard_files(dirpath)}
        old = {}
        for name, f in existing.items():
            with open(f) as fh:
                old[name] = json.load(fh)
        grow, shrink = [], []
        for name in sorted(set(want) | set(old)):
            sy, le = want.get(name, ({}, {}))
            o = old.get(name)
            if o is not None and o.get('systems') == dict(sorted(sy.items())) and o.get('lessons') == dict(sorted(le.items())):
                continue
            if o is not None and not sy and not le and name != GENERAL_SHARD:
                shrink.append((name, None)); continue
            gone = o is not None and (set(o.get('systems', {})) - set(sy) or set(o.get('lessons', {})) - set(le))
            (shrink if gone else grow).append((name, (sy, le)))
        for name, recs in grow + shrink:
            path = os.path.join(dirpath, f'{name}.json')
            if recs is None:
                if backup:
                    self._backup(path)
                os.remove(path); continue
            meta = dict((old.get(name) or {}).get('kb_meta') or {'schema_version': SCHEMA_VERSION, 'created': now(),
                                                                   'origin': []})
            meta['shard'] = name
            part = KB({'kb_meta': meta, 'systems': copy.deepcopy(recs[0]), 'lessons': copy.deepcopy(recs[1])},
                      path=path, profiles=self.profiles)
            part.save(path, backup=backup)
        _write_conflicts(os.path.join(dirpath, CONFLICTS_FILE), self.conflicts)

    @classmethod
    def load_sources(cls, sources, profiles=None):
        '''merge several sources [(role, path)] (KB files or shard directories) into one KB that remembers where
        each record came from; write it back with save_sources() (or use transaction(sources))'''
        sources = [(r, os.path.abspath(p)) for r, p in sources]
        for r, _ in sources:
            if r not in ROLES:
                raise KBError(f'source role must be one of {ROLES}, not {r!r}')
        if not any(r == SHARED for r, _ in sources):
            raise KBError('the sources need one shared KB')
        parts = [cls.load(p, profiles) for _, p in sources]
        kb, _ = cls.merge(parts, prefer='newer', profiles=profiles)
        kb.path, kb.sources = None, sources
        for (_, p), part in zip(sources, parts):
            for kind in ('systems', 'lessons'):
                for i in part.data[kind]:
                    kb.origin.setdefault((kind, i), set()).add(p)
            for cid in part.conflicts:
                kb.conflict_origin.setdefault(cid, p)
        kb.meta['origin'] = [{'sources': [f'{r}:{p}' for r, p in sources]}]
        _apply_backlinks(kb.lessons, kb.conflicts)
        return kb

    def _kind(self, rid):
        kinds = [k for k in ('systems', 'lessons') if rid in self.data[k]]
        if not kinds:
            raise KBError(f'no system or lesson {rid!r}')
        return kinds[0]

    def place(self, rid, role):
        '''store record rid (system or lesson) in the first source of this role from now on'''
        if role not in ROLES:
            raise KBError(f'role must be one of {ROLES}')
        if self.sources is not None and not any(r == role for r, _ in self.sources):
            raise KBError(f'no {role} KB is loaded')
        kind = self._kind(rid)
        if (kind, rid) in self.origin:
            self._guard(f'moving the existing record {rid}')
        self.placement[(kind, rid)] = role

    def targets(self, kind, rid):
        '''source paths a record is written to'''
        first = {}
        for r, p in self.sources:
            first.setdefault(r, p)
        if (kind, rid) in self.placement:
            return {first[self.placement[(kind, rid)]]}
        if (kind, rid) in self.origin:
            return self.origin[(kind, rid)]
        return {first.get(route_new(kind, self.data[kind][rid]), first[SHARED])}

    def role_of(self, kind, rid):
        roles = {r for r, p in self.sources if p in self.targets(kind, rid)}
        return SHARED if SHARED in roles else PROJECT

    def source_parts(self):
        '''{path: KB} with the records each source will hold'''
        parts = {p: KB({'kb_meta': {'schema_version': SCHEMA_VERSION, 'created': now(), 'origin': []},
                        'systems': {}, 'lessons': {}}, profiles=self.profiles) for _, p in self.sources}
        for kind in ('systems', 'lessons'):
            for i, rec in self.data[kind].items():
                for p in self.targets(kind, i):
                    parts[p].data[kind][i] = rec
        first = {}
        for r, p in self.sources:
            first.setdefault(r, p)
        for cid, c in self.conflicts.items():
            p = self.conflict_origin.get(cid) or first.get(self.conflict_role(c), first[SHARED])
            parts[p].conflicts[cid] = c
        return parts

    def conflict_role(self, c):
        '''where a new conflict is stored: shared when every lesson it contradicts is shared and its finding would
        be shared, else project'''
        if self.sources is None:
            return SHARED
        roles = {self.role_of('lessons', x) for x in c.get('contradicts', []) if x in self.lessons}
        if not roles and c.get('kind') == 'direct':
            roles = {c.get('role', SHARED)}
        finding_ok = not c.get('finding') or route_new('lessons', c['finding']) == SHARED
        return SHARED if roles == {SHARED} and finding_ok else PROJECT

    def check_sources(self):
        '''(errors, warnings) for the split into sources: a shared source must make sense without the project
        KBs, so it may not reference records that live only in a project KB; home-directory paths are flagged'''
        E, W = [], []
        if self.sources is None:
            return E, W
        roles = dict((p, r) for r, p in self.sources)
        homes = _home_dirs()
        for p, part in self.source_parts().items():
            if roles[p] != SHARED:
                continue
            for lid, l in part.lessons.items():
                for x in (l.get('applies_to') or {}).get('systems', []):
                    if x not in part.systems and x in self.systems:
                        E.append(f'shared lesson {lid} applies to system {x}, which is only in a project KB: '
                                 'move the lesson to the project KB or drop the reference')
                for x in successors(l):
                    if x not in part.lessons and x in self.lessons:
                        E.append(f'shared lesson {lid} is superseded by {x}, which is only in a project KB: '
                                 'move one of them')
                for r in l.get('references') or []:
                    if r['id'] not in part.lessons and r['id'] in self.lessons:
                        E.append(f"shared lesson {lid} references {r['id']}, which is only in a project KB")
                for e in l.get('evidence', []):
                    if any(str(e.get('path', '')).startswith(h) for h in homes):
                        W.append(f"shared lesson {lid} cites {e['path']}, in a home directory that others cannot read")
            for cid, c in part.conflicts.items():
                if c.get('status') == 'open':
                    for x in c.get('contradicts', []):
                        if x not in part.lessons and x in self.lessons:
                            E.append(f'shared conflict {cid} contradicts {x}, which is only in a project KB')
        return E, W

    def save_sources(self, backup=True):
        '''write every record back to its source(s); sources that gain records are written first, so an
        interruption can duplicate a moved record (merged away on load) but never lose one'''
        parts = self.source_parts()
        order = []
        for p, part in parts.items():
            old = KB.load(p, self.profiles) if os.path.exists(p) and (not os.path.isdir(p) or
                                                                       _shard_files(p)) else None
            loses = old is not None and (set(old.systems) - set(part.systems) or set(old.lessons) - set(part.lessons))
            order.append((bool(loses), p, part, old))
        for _, p, part, old in sorted(order, key=lambda x: x[0]):
            if os.path.isdir(p) or not p.endswith('.json'):
                part.save_shards(p, backup=backup)
            else:
                if old is not None:
                    part.data['kb_meta'] = dict(old.meta)
                part.save(p, backup=backup)
        self.origin = {}
        for p, part in parts.items():
            for kind in ('systems', 'lessons'):
                for i in part.data[kind]:
                    self.origin.setdefault((kind, i), set()).add(p)
            for cid in part.conflicts:
                self.conflict_origin[cid] = p
        self.placement = {}

    @staticmethod
    def _backup(path):
        bdir = os.path.join(os.path.dirname(os.path.abspath(path)), 'backups'); os.makedirs(bdir, exist_ok=True)
        stamp = datetime.datetime.now().strftime('%Y%m%dT%H%M%S%f')
        with open(path) as f, open(os.path.join(bdir, f'{os.path.basename(path)}.{stamp}'), 'w') as g:
            g.write(f.read())
        old = sorted(glob.glob(os.path.join(bdir, f'{os.path.basename(path)}.*')))
        for o in old[:-N_BACKUPS]:
            os.remove(o)

    # ------------------------------------------------------------ validation
    def validate(self, strict=False):
        '''returns (errors, warnings); strict turns dangling references into errors'''
        E, W = [], []
        dangling = E if strict else W
        if self.meta.get('schema_version') != SCHEMA_VERSION:
            E.append('kb_meta.schema_version mismatch')
        for sid, s in self.systems.items():
            tag = f'system {sid}'
            extra = set(s) - set(SYSTEM_KEYS)
            if extra:
                E.append(f'{tag}: unknown field(s) {sorted(extra)}')
            prof = self.profiles.get(s.get('profile'))
            if prof is None:
                E.append(f"{tag}: unknown profile {s.get('profile')!r}"); continue
            try:
                ident = prof.normalize_identity(s.get('identity'))
            except (ValueError, TypeError) as e:
                E.append(f'{tag}: {e}'); continue
            if ident != s['identity']:
                E.append(f'{tag}: identity is not in normalized form ({ident})')
            if s.get('profile_version') != prof.version:
                W.append(f"{tag}: profile_version {s.get('profile_version')} != {prof.name} {prof.version}; run rehash")
            elif prof.canonical(ident) != s.get('canonical') or prof.system_id(ident) != sid or s.get('id') != sid:
                E.append(f'{tag}: id/canonical do not match the profile (expected {prof.system_id(ident)})')
            ids = [m.get('id') for m in s.get('models', [])]
            if len(ids) != len(set(ids)) or None in ids:
                E.append(f'{tag}: model ids must be present and unique')
            for m in s.get('models', []):
                bad = set(m) - set(MODEL_KEYS)
                if bad:
                    E.append(f"{tag} model {m.get('id')}: unknown field(s) {sorted(bad)}")
                if m.get('status') not in MODEL_STATUS:
                    E.append(f"{tag} model {m.get('id')}: status must be one of {MODEL_STATUS}")
            if s.get('best_model') and s['best_model'] not in ids:
                E.append(f"{tag}: best_model {s['best_model']} is not a model id")
            if (s.get('review') or {}).get('level') not in REVIEW_LEVELS:
                E.append(f'{tag}: review.level must be one of {REVIEW_LEVELS}')
            for u in s.get('user_decisions', []):
                if not (isinstance(u, dict) and u.get('text') and u.get('date')):
                    E.append(f'{tag}: user_decisions entries need text and date')
            if not all(isinstance(t, str) for t in s.get('tags', [])):
                E.append(f'{tag}: tags must be strings')
            pe, pw = prof.validate_system(s)
            E += [f'{tag}: {x}' for x in pe]; W += pw
        for lid, l in self.lessons.items():
            tag = f'lesson {lid}'
            extra = set(l) - set(LESSON_KEYS)
            if extra:
                E.append(f'{tag}: unknown field(s) {sorted(extra)}')
            if any(not l.get(k) for k in ('title', 'text', 'category', 'date')):
                E.append(f'{tag}: title, text, category and date are required'); continue
            if lesson_id(l['category'], l['title'], l['text'], l['date']) != lid or l.get('id') != lid:
                E.append(f'{tag}: id does not match its content (edited in place? supersede instead)')
            at = l.get('applies_to') or {}
            if set(at) - set(APPLIES_KEYS):
                E.append(f'{tag}: unknown applies_to key(s) {sorted(set(at) - set(APPLIES_KEYS))}')
            for k in SETUP_KEYS:
                if any(not isinstance(v, str) or not v or v != norm_setup(v) for v in at.get(k, [])):
                    E.append(f'{tag}: applies_to.{k} values must be non-empty lower-case tokens without spaces')
            if l.get('status') == 'active' and l['category'] == 'software' and not at.get('software'):
                W.append(f'{tag}: a software lesson without applies_to.software reaches users of every code')
            if l.get('scope') not in SCOPES:
                E.append(f'{tag}: scope must be one of {SCOPES}')
            elif l['scope'] == 'system' and not at.get('systems'):
                E.append(f'{tag}: scope "system" needs applies_to.systems')
            elif l['scope'] == 'profile' and not at.get('profiles'):
                E.append(f'{tag}: scope "profile" needs applies_to.profiles')
            elif l['scope'] == 'family' and not at.get('tags'):
                E.append(f'{tag}: scope "family" needs applies_to.tags')
            for p in at.get('profiles', []):
                if p not in self.profiles:
                    E.append(f'{tag}: unknown profile {p!r} in applies_to')
            for s in at.get('systems', []):
                if s not in self.systems:
                    dangling.append(f'{tag}: applies_to system {s} is not in this KB')
            cats = set(CORE_CATEGORIES)
            profs = set(at.get('profiles', [])) | {self.systems[s]['profile'] for s in at.get('systems', []) if s in self.systems}
            for p in profs:
                if p in self.profiles:
                    cats |= set(self.profiles[p].lesson_categories)
            if l['category'] not in cats:
                E.append(f"{tag}: category {l['category']!r} not in the core categories or those of {sorted(profs) or 'its profiles'}")
            ev = l.get('evidence')
            if not ev or not all(isinstance(e, dict) and e.get('path') for e in ev):
                E.append(f'{tag}: evidence must be a non-empty list of {{path, note}}')
            for k, allowed in (('confidence', CONFIDENCE), ('source', SOURCES), ('status', LESSON_STATUS)):
                if l.get(k) not in allowed:
                    E.append(f'{tag}: {k} must be one of {allowed}')
            sb = l.get('superseded_by')
            if sb is not None and not (isinstance(sb, str) or (isinstance(sb, list) and sb and
                                                                 all(isinstance(x, str) for x in sb))):
                E.append(f'{tag}: superseded_by must be a lesson id or a non-empty list of ids')
            elif l.get('status') == 'superseded':
                if not sb:
                    E.append(f'{tag}: superseded without superseded_by')
                for x in successors(l):
                    if x == lid:
                        E.append(f'{tag}: superseded by itself')
                    elif x not in self.lessons:
                        dangling.append(f'{tag}: superseded_by {x} is not in this KB')
            if l.get('status') == 'active' and len(l['text']) > LESSON_TEXT_WARN:
                W.append(f"{tag}: text is {len(l['text'])} characters; one lesson, one statement (GUIDE §4 rule 3): "
                         'consider splitting it')
            # metadata (GUIDE §3): evidence keys, coverage, verification
            for e in ev or []:
                try:
                    norm_evidence_entry(e)
                except KBError as x:
                    E.append(f'{tag}: {x}'); continue
                if set(e) - set(EVIDENCE_KEYS):
                    W.append(f"{tag}: evidence {e['path']} has unknown key(s) {sorted(set(e) - set(EVIDENCE_KEYS))}")
            for k, norm in (('coverage', lambda v: _str_list(v, 'coverage')), ('verification', norm_verification)):
                if k in l:
                    try:
                        norm(l[k])
                    except KBError as x:
                        E.append(f'{tag}: {x}')
            words = universal_words(l) if l.get('status') == 'active' else []
            if words and not l.get('coverage'):
                W.append(f"{tag}: says {', '.join(repr(w) for w in words)} without coverage (GUIDE §4 rule 9): list "
                         'the cases the evidence covers in `coverage`, or narrow the wording in a reconciliation session')
        # links between lessons
        for lid, l in self.lessons.items():
            try:
                refs = norm_references(l.get('references'))
            except KBError as e:
                E.append(f'lesson {lid}: {e}'); continue
            for r in refs:
                if r['id'] == lid:
                    E.append(f'lesson {lid} references itself')
                elif r['id'] not in self.lessons:
                    dangling.append(f"lesson {lid}: references {r['id']}, which is not in this KB")
        # structured context (GUIDE §3): its shape, the profile's observables, and how many lessons still lack it
        missing = 0
        for lid, l in self.lessons.items():
            if 'context' in l:
                try:
                    c = norm_context(l['context'])
                except KBError as e:
                    E.append(f'lesson {lid}: {e}'); continue
                for p in (l.get('applies_to') or {}).get('profiles', []):
                    prof = self.profiles.get(p)
                    vocab = {v.lower() for v in getattr(prof, 'observables', {}) or {}}
                    if vocab and getattr(prof, 'strict_tags', False):
                        for o in c['observables']:
                            if o.split(' (')[0].strip().lower() not in vocab:
                                W.append(f"lesson {lid}: observable {o!r} is not in profile {p}'s list")
            if l.get('status') == 'active' and not context_ok(l.get('context')):
                missing += 1
        if missing:
            W.append(f'{missing} active lessons have no context yet (filled during reconciliation, GUIDE §12)')
        gone = {a.get('id') for c in self.conflicts.values() for a in (c.get('resolution') or {}).get('archived', [])}
        for cid, c in self.conflicts.items():
            tag = f'conflict {cid}'
            if c.get('status') not in CONFLICT_STATUS:
                E.append(f'{tag}: status must be one of {CONFLICT_STATUS}')
            if not c.get('contradicts') or not c.get('disagreement'):
                E.append(f'{tag}: needs contradicts and disagreement')
            if not context_ok(c.get('context')) or not ((c.get('finding') or {}).get('text') or c.get('kind') == 'direct' or
                                                         (c.get('kind') == 'internal' and len(c.get('contradicts', [])) >= 2)):
                E.append(f'{tag}: needs a finding (or two contradicting lessons) and a context')
            if c.get('status') == 'open':
                for x in c.get('contradicts', []):
                    if x not in self.lessons:
                        dangling.append(f'{tag}: contradicts {x}, which is not in this KB')
            else:
                r = c.get('resolution') or {}
                if r.get('action') not in RESOLUTION_ACTIONS:
                    E.append(f'{tag}: a closed conflict needs resolution.action from {RESOLUTION_ACTIONS}')
                for x in r.get('result_ids', []):
                    if x not in self.lessons and x not in gone:
                        W.append(f'{tag}: result {x} is not in this KB')
        # family tags are alternatives (any one matches), so a broad tag next to a narrower one widens the lesson
        for lid, l in self.lessons.items():
            at = l.get('applies_to') or {}
            tags = at.get('tags', [])
            if l.get('status') != 'active' or l.get('scope') != 'family' or len(tags) < 2:
                continue
            pool = [s for s in self.systems.values() if not at.get('profiles') or s['profile'] in at['profiles']]
            reach = {t: {s['id'] for s in pool if Profile.tag_match(t, s.get('tags', []))} for t in tags}
            for a in tags:
                for b in tags:
                    if a != b and (b.startswith(a + ':') or (reach[b] and reach[b] < reach[a])):
                        W.append(f'lesson {lid}: tag {a!r} already matches every system {b!r} does '
                                 f'({len(reach[a])} vs {len(reach[b])} systems). Tags are alternatives, so the lesson '
                                 f'reaches all {a!r} systems; keep only the tag that names the class it holds for '
                                 '(GUIDE §3 "Applicability")')
        # a replacement that copies sentences of a seeded, unreviewed lesson must keep the seeded flag
        for lid, l in self.lessons.items():
            for x in successors(l):
                n = self.lessons.get(x)
                if (l.get('seeded_from_docs') and not l.get('reviewed') and n and n.get('status') == 'active'
                        and not n.get('seeded_from_docs') and not n.get('reviewed')
                        and any(t in n['text'] for t in _sentences(l['text']))):
                    W.append(f'lesson {x}: copies text of the seeded, unreviewed {lid} but is not marked '
                             'seeded_from_docs (GUIDE §4 rule 5)')
        return E, W

    def check(self, strict=False):
        E, _ = self.validate(strict)
        if E:
            raise KBError('validation failed:\n  ' + '\n  '.join(E))

    # ------------------------------------------------------------ writes
    def add_system(self, rec, update=False):
        sid = rec['id']
        if not sid:
            raise KBError('cannot add a partial (query) system')
        if sid in self.systems:
            if not update:
                raise KBError(f"system {sid} ({rec.get('alias')}) already exists; use update")
            conflicts = []
            old = self.systems[sid]
            merged = _merge_val(old, rec, [sid], conflicts, prefer_b=True)
            changed = [c for c in conflicts if not c['path'].endswith(('/updated', '/created', '/date'))]
            if changed and self.mode == 'general':
                raise KBError(f"system {sid}: {', '.join(c['path'] for c in changed)} already set; {GENERAL_REFUSAL}")
            merged['updated'] = now(); merged['created'] = old.get('created', merged.get('created'))
            self.systems[sid] = merged
            return conflicts
        self.systems[sid] = copy.deepcopy(rec)
        return []

    def update_system(self, sid, fields):
        '''set top-level fields of a system (not identity/id/profile: those define it)'''
        s = self._system(sid)
        for k in fields:
            if k in ('id', 'profile', 'profile_version', 'canonical', 'identity') or k not in SYSTEM_KEYS:
                raise KBError(f'cannot set system field {k!r}')
            if self.mode == 'general' and not _fill_ok(s.get(k), fields[k]):
                raise KBError(f'system {sid}: {k!r} is already set; general sessions only add to lists and fill empty '
                              f'fields; {GENERAL_REFUSAL}')
        s.update(copy.deepcopy(fields)); s['updated'] = now()

    def add_model(self, sid, model, update=False):
        s = self._system(sid)
        model = dict(model); model.setdefault('date', today()); model['updated'] = now()
        for m in s['models']:
            if m['id'] == model['id']:
                if not update:
                    raise KBError(f"system {sid} already has model {model['id']}; use update")
                if self.mode == 'general':
                    if m.get('date'):
                        model['date'] = m['date']
                    for k, v in model.items():
                        if k in ('id', 'updated', 'date') or _fill_ok(m.get(k), v):
                            continue
                        if k == 'status' and m.get(k) in ('planned', 'pending', 'running'):
                            continue
                        raise KBError(f"model {model['id']} of {sid}: {k!r} is already set; general sessions fill a "
                                      f'model once (status from planned/pending/running, empty fields); {GENERAL_REFUSAL}')
                m.update(model); s['updated'] = now(); return
        s['models'].append(model); s['updated'] = now()

    def add_lesson(self, d):
        l = make_lesson(d)
        if l['id'] in self.lessons:
            raise KBError(f"lesson {l['id']} already exists ({l['title']!r})")
        self._check_refs(l)
        self.lessons[l['id']] = l
        return l['id']

    def update_lesson(self, lid, fields, replace=False):
        '''set mutable fields. Normal sessions may edit only the metadata (LESSON_METADATA); the rest needs
        reconcile mode. evidence entries are matched by path (and by index or lines when several cite one path):
        their keys are set (None removes one), and entries with a new path are appended. Evidence is never removed
        and its path never changes: supersede or replace the lesson instead. applies_to is extended, or replaced
        with replace=True (to narrow where a lesson applies); other fields are set.'''
        if self.mode == 'general':
            bad = [k for k in fields if k not in LESSON_METADATA]
            if bad or replace:
                self._guard(f"editing {', '.join(bad) or 'applies_to'} of lesson {lid} (normal sessions may edit "
                            f"only the metadata: {', '.join(LESSON_METADATA)})")
        l = self._lesson(lid)
        for k in fields:
            if k in LESSON_IMMUTABLE or k not in LESSON_KEYS:
                raise KBError(f'cannot edit lesson field {k!r}; supersede the lesson instead')
        for k, v in fields.items():
            if k == 'evidence':
                l['evidence'] = self._patch_evidence(lid, l['evidence'], v)
            elif k == 'coverage':
                l['coverage'] = _str_list(v, 'coverage')
            elif k == 'verification':
                l['verification'] = norm_verification(v)
            elif k == 'reviewed':
                if not isinstance(v, bool):
                    raise KBError('reviewed must be true or false')
                l['reviewed'] = v
            elif k == 'references':
                l['references'] = norm_references(v)
                self._check_refs(l)
            elif k == 'referenced_by':
                raise KBError('referenced_by is derived; set references on the lesson that points')
            elif k == 'applies_to' and replace:
                if set(v) - set(APPLIES_KEYS):
                    raise KBError(f'unknown applies_to key(s) {sorted(set(v) - set(APPLIES_KEYS))}')
                l['applies_to'] = {kk: sorted({norm_setup(x) for x in v.get(kk, [])}) if kk in SETUP_KEYS
                                   else list(v.get(kk, [])) for kk in APPLIES_KEYS}
            elif k == 'applies_to':
                if set(v) - set(APPLIES_KEYS):
                    raise KBError(f'unknown applies_to key(s) {sorted(set(v) - set(APPLIES_KEYS))}')
                for kk, vv in v.items():
                    if kk in SETUP_KEYS:
                        vv = [norm_setup(x) for x in vv]
                    l['applies_to'][kk] = sorted(set(l['applies_to'].get(kk, [])) | set(vv))
            else:
                l[k] = copy.deepcopy(v)
        l['updated'] = now()

    @staticmethod
    def _patch_evidence(lid, current, patch):
        out = [dict(e) for e in current]
        for p in [patch] if isinstance(patch, dict) else patch:
            if not isinstance(p, dict) or not p.get('path'):
                raise KBError(f'evidence patches are {{path, ...}}, not {p!r}')
            p = dict(p); idx = p.pop('index', None)
            hits = [i for i, e in enumerate(out) if e['path'] == p['path']]
            if idx is not None:
                if idx not in hits:
                    raise KBError(f"lesson {lid}: evidence entry {idx} does not cite {p['path']}")
                hits = [idx]
            elif len(hits) > 1:
                same = [i for i in hits if p.get('lines') and out[i].get('lines') == norm_lines(p['lines'])]
                if len(same) != 1:
                    raise KBError(f"lesson {lid}: {len(hits)} evidence entries cite {p['path']} (entries {hits}); "
                                  'give "index" to choose one')
                hits = same
            if not hits:
                if not p.get('note'):
                    raise KBError(f"lesson {lid}: a new evidence entry ({p['path']}) needs a note")
                if set(p) & {'supplement', 'sha256'}:
                    raise KBError('supplement and sha256 are written by `kbcli supplement`')
                out.append(norm_evidence_entry({k: v for k, v in p.items() if v is not None}))
                continue
            e = out[hits[0]]
            for k, v in p.items():
                if k == 'path':
                    continue
                if k in ('supplement', 'sha256'):
                    if v != e.get(k):
                        raise KBError('supplement and sha256 are written by `kbcli supplement`')
                elif v is None:
                    e.pop(k, None)
                else:
                    e[k] = v
            out[hits[0]] = norm_evidence_entry(e)
        return out

    def add_verification(self, lid, record):
        '''append one verification record {verdict, by, method, note?, date?} to a lesson (allowed in any mode)'''
        l = self._lesson(lid)
        l['verification'] = norm_verification(l.get('verification')) + norm_verification(record)
        l['updated'] = now()

    def supersede(self, old, new):
        '''mark lesson old superseded by new: one id, or a list of ids when the lesson is split'''
        self._guard(f'superseding lesson {old}')
        lo = self._lesson(old)
        new = [new] if isinstance(new, str) else list(dict.fromkeys(new))
        if not new:
            raise KBError('supersede needs at least one replacing lesson')
        for n in new:
            self._lesson(n)
            if n == old:
                raise KBError('a lesson cannot supersede itself')
        lo['status'], lo['superseded_by'], lo['updated'] = 'superseded', new[0] if len(new) == 1 else new, now()

    def related_lessons(self, lesson, top=8, min_score=0.1, exclude=()):
        '''[(score, lesson)]: active lessons most likely to overlap or conflict with `lesson` (a record or a draft
        dict), best first. Score = Jaccard overlap of content words, plus small bonuses for the same category and
        shared systems, tags or profiles. A reminder for the writer, not a verdict.'''
        ta, A = lesson_terms(lesson), lesson.get('applies_to') or {}
        skip = set(exclude) | {lesson.get('id')}
        res = []
        for b in self.lessons.values():
            if b['status'] != 'active' or b['id'] in skip:
                continue
            tb, B = lesson_terms(b), b.get('applies_to') or {}
            score = (len(ta & tb) / max(1, len(ta | tb)) + 0.05 * (lesson.get('category') == b['category'])
                     + 0.1 * bool(set(A.get('systems', [])) & set(B.get('systems', [])))
                     + 0.05 * bool(set(A.get('tags', [])) & set(B.get('tags', [])))
                     + 0.02 * bool(set(A.get('profiles', [])) & set(B.get('profiles', []))))
            if score >= min_score:
                res.append((round(score, 3), b))
        res.sort(key=lambda r: (-r[0], r[1]['id']))
        return res[:top]

    # ------------------------------------------------------------ conflicts and reconciliation (GUIDE §12)
    def add_conflict(self, finding, contradicts, why, author='', context=None, evidence=None):
        '''record a new finding that contradicts existing lessons, instead of adding it; allowed in every mode.
        finding=None records an internal conflict between two or more existing lessons (give context).'''
        if finding is None:
            f = None
            context = norm_context(context)
            if len(set(contradicts or [])) < 2 or not context_ok(context):
                raise KBError('an internal conflict needs two or more lessons and a context')
        else:
            f = make_lesson(finding)
            context = f.get('context')
            if not context_ok(context):
                raise KBError('a conflict needs the finding\'s context: {systems, observables, methods, conditions}')
        if not contradicts:
            raise KBError('name the lessons the finding contradicts')
        for x in contradicts:
            if self._lesson(x)['status'] != 'active':
                raise KBError(f'{x} is not active')
        if not (why or '').strip():
            raise KBError('say what disagrees (--why)')
        key = f['id'] if f else why.strip()
        cid = 'C-' + hashlib.sha256(('|'.join(sorted(set(contradicts))) + '|' + key).encode()).hexdigest()[:10]
        if cid in self.conflicts:
            raise KBError(f'conflict {cid} already exists')
        self.conflicts[cid] = {'id': cid, 'date': today(), 'author': author or (f or {}).get('author', ''),
                               'status': 'open', 'kind': 'finding' if f else 'internal',
                               'contradicts': sorted(set(contradicts)), 'disagreement': why.strip(),
                               'context': context, 'finding': f, 'updated': now()}
        if evidence:
            if not all(isinstance(e, dict) and e.get('path') for e in evidence):
                raise KBError('conflict evidence must be a list of {path, note}')
            self.conflicts[cid]['evidence'] = copy.deepcopy(evidence)
        return cid

    def contested(self, lid):
        '''ids of the open conflicts that contradict lesson lid'''
        return sorted(c for c, r in self.conflicts.items() if r.get('status') == 'open' and lid in r['contradicts'])

    def _conflict(self, cid):
        if cid not in self.conflicts:
            raise KBError(f'no conflict {cid}')
        return self.conflicts[cid]

    def adopt_finding(self, cid):
        '''add a conflict's finding to the KB as a lesson (reconciliation); returns its id'''
        self._guard(f'adopting the finding of {cid}')
        c = self._conflict(cid)
        if not c.get('finding'):
            raise KBError(f'{cid} is an internal conflict: it has no finding to adopt')
        d = {k: v for k, v in c['finding'].items() if k not in ('status', 'superseded_by', 'updated')}
        return self.add_lesson(d)

    def _check_refs(self, l):
        for r in l.get('references') or []:
            if r['id'] == l['id'] or r['id'] not in self.lessons:
                raise KBError(f"lesson {l['id']}: reference {r['id']} is not another lesson in this KB")

    def suggest_mapping(self, old_records, new):
        '''{old id: [new id]}: for each old lesson record, the new lesson whose wording overlaps most'''
        out = {}
        for o in old_records:
            to = lesson_terms(o)
            best = max(new, key=lambda n: (len(to & lesson_terms(self.lessons[n])) /
                                            max(1, len(to | lesson_terms(self.lessons[n]))), n))
            out[o['id']] = [best]
        return out

    def replace(self, old, new, mapping=None):
        '''remove lessons old (reconciliation) and point every reference to each one (superseded_by, references,
        open conflicts) at the lessons that take over its content; returns the removed records, to be archived in
        the conflict's resolution. mapping {old: [new, ...]} is needed when several lessons replace several;
        "auto" pairs each old lesson with the new one whose wording overlaps most (suggest_mapping).'''
        self._guard('replacing lessons')
        old = [old] if isinstance(old, str) else list(old)
        new = [new] if isinstance(new, str) else list(new)
        for x in old + new:
            self._lesson(x)
        if set(old) & set(new):
            raise KBError('a lesson cannot replace itself')
        if mapping == 'auto':
            mapping = self.suggest_mapping([self.lessons[x] for x in old], new)
        if mapping is None:
            if len(old) > 1 and len(new) > 1:
                raise KBError('several lessons replaced by several: give the mapping {old: [new, ...]} '
                              '(kbcli: --map OLD=NEW[,NEW] ... or --map auto)')
            mapping = {o: list(new) for o in old}
        if set(mapping) != set(old) or not all(mapping[o] and set(mapping[o]) <= set(new) for o in old):
            raise KBError('the mapping must give every old lesson one or more of the new lessons')
        archived = [self.lessons.pop(x) for x in old]
        def swap(ids):
            out = []
            for i in ids:
                for j in (mapping[i] if i in mapping else [i]):
                    if j not in out:
                        out.append(j)
            return out
        for l in self.lessons.values():
            if any(x in old for x in successors(l)):
                s = swap(successors(l)); l['superseded_by'] = s[0] if len(s) == 1 else s; l['updated'] = now()
            refs = l.get('references') or []
            if any(r['id'] in old for r in refs):
                l['references'] = norm_references([dict(r, id=j) for r in refs for j in swap([r['id']])
                                                   if j != l['id']])
                l['updated'] = now()
        for c in self.conflicts.values():
            if c.get('status') == 'open' and any(x in old for x in c['contradicts']):
                c['contradicts'] = swap(c['contradicts']); c['updated'] = now()
        _apply_backlinks(self.lessons, self.conflicts)
        return archived

    def remove(self, lids):
        '''remove lessons without a successor (reconciliation, e.g. on a user decision); returns the records for
        record_direct() to archive. Refused while another lesson is superseded by one of them or an open conflict
        names one.'''
        self._guard('removing lessons')
        lids = [lids] if isinstance(lids, str) else list(lids)
        for x in lids:
            self._lesson(x)
            sole = [l['id'] for l in self.lessons.values() if successors(l) and set(successors(l)) <= set(lids)]
            if sole or self.contested(x):
                raise KBError(f'{x} is still needed by {sole + self.contested(x)} (their only successor, or an open '
                              'conflict); resolve or replace those first')
        for l in self.lessons.values():                 # drop removed ids from successor lists that keep others
            if any(x in lids for x in successors(l)):
                rest = [y for y in successors(l) if y not in lids]
                l['superseded_by'] = rest[0] if len(rest) == 1 else rest; l['updated'] = now()
        return [self.lessons.pop(x) for x in lids]

    def record_direct(self, action, lids, why, by='', archived=(), result_ids=()):
        '''a closed record of a reconciliation step taken without a logged conflict (e.g. a user decision), so every
        change to existing lessons is on file in conflicts.json'''
        self._guard('recording a reconciliation step')
        if action not in RESOLUTION_ACTIONS:
            raise KBError(f'action must be one of {RESOLUTION_ACTIONS}')
        if not lids or not (why or '').strip():
            raise KBError('name the lessons and say why')
        ctx = None
        for l in list(archived) + [self.lessons[x] for x in list(lids) + list(result_ids) if x in self.lessons]:
            if context_ok(l.get('context')):
                ctx = l['context']; break
        if not context_ok(ctx):
            raise KBError('the lessons need a context')
        cid = 'C-' + hashlib.sha256(('direct|' + '|'.join(sorted(lids)) + '|' + action + '|' + why).encode()).hexdigest()[:10]
        self.conflicts[cid] = {'id': cid, 'date': today(), 'author': by, 'status': 'resolved', 'kind': 'direct',
                               'contradicts': sorted(lids), 'disagreement': why.strip(), 'context': ctx, 'finding': None,
                               'resolution': {'action': action, 'result_ids': list(result_ids),
                                              'archived': list(archived), 'note': why.strip(), 'by': by,
                                              'date': today()},
                               'updated': now()}
        return cid

    def resolve_conflict(self, cid, action, result_ids=(), archived=(), note='', by=''):
        '''close a conflict (reconciliation): action from RESOLUTION_ACTIONS, the lessons that now carry the
        knowledge, and the records replace() removed'''
        self._guard(f'resolving {cid}')
        c = self._conflict(cid)
        if action not in RESOLUTION_ACTIONS:
            raise KBError(f'action must be one of {RESOLUTION_ACTIONS}')
        if action != 'reject':
            if not result_ids:
                raise KBError('name the lessons that carry the result (--result)')
            for x in result_ids:
                self._lesson(x)
        c['status'] = 'rejected' if action == 'reject' else 'resolved'
        c['resolution'] = {'action': action, 'result_ids': list(result_ids), 'archived': list(archived),
                           'note': note, 'by': by, 'date': today()}
        c['updated'] = now()

    def _system(self, sid):
        if sid not in self.systems:
            hits = self.find(sid)
            if len(hits) != 1:
                raise KBError(f'system {sid!r}: {len(hits)} matches')
            sid = hits[0]['id']
        return self.systems[sid]

    def _lesson(self, lid):
        if lid not in self.lessons:
            raise KBError(f'no lesson {lid}')
        return self.lessons[lid]

    # ------------------------------------------------------------ queries
    def find(self, q):
        '''exact id, else case-insensitive substring of alias or canonical'''
        if q in self.systems:
            return [self.systems[q]]
        ql = q.lower()
        return [s for s in self.systems.values() if ql in s['alias'].lower() or ql in s['canonical'].lower()]

    def applies(self, lesson, system, setup=None):
        '''chemistry (scope) test, then the setup test: a lesson whose setup condition conflicts with the declared
        setup does not apply; one conditional on an undeclared key does (callers flag it with setup_match)'''
        if setup_match(lesson, setup) is False:
            return False
        at = lesson.get('applies_to') or {}
        if system.get('id') and system['id'] in at.get('systems', []):
            return True
        sc = lesson['scope']
        if sc == 'general':
            return True
        if sc == 'profile':
            return system['profile'] in at.get('profiles', [])
        if sc == 'family':
            if at.get('profiles') and system['profile'] not in at['profiles']:
                return False
            return any(Profile.tag_match(t, system.get('tags', [])) for t in at.get('tags', []))
        return False

    def lessons_for(self, system=None, profile=None, tag=None, category=None, scope=None, include_superseded=False,
                    setup=None):
        '''setup: {"software": [...], "platform": [...]} declared by the caller; lessons conditional on a conflicting
        setup are dropped, lessons conditional on an undeclared key are kept (see setup_match)'''
        out = []
        for l in self.lessons.values():
            if l['status'] == 'superseded' and not include_superseded:
                continue
            if setup_match(l, setup) is False:
                continue
            if system is not None and not self.applies(l, system):
                continue
            at = l.get('applies_to') or {}
            if profile and not (l['scope'] == 'general' or profile in at.get('profiles', []) or
                                any(self.systems.get(s, {}).get('profile') == profile for s in at.get('systems', []))):
                continue
            if tag and not (tag in l.get('tags', []) or tag in at.get('tags', [])):
                continue
            if category and l['category'] != category:
                continue
            if scope and l['scope'] != scope:
                continue
            out.append(l)
        order = {s: i for i, s in enumerate(SCOPES)}
        return sorted(out, key=lambda l: (order[l['scope']], l['category'], l['date'], l['id']))

    def similar(self, query, top=None, min_score=0.0, exclude_self=False):
        '''[(score, breakdown, system)] for systems of the query's profile, best first'''
        prof = self.profiles[query['profile']]
        res = []
        for s in self.systems.values():
            if s['profile'] != prof.name or (exclude_self and s['id'] == query.get('id')):
                continue
            score, parts = prof.similarity(query, s)
            if score >= min_score:
                res.append((round(score, 4), parts, s))
        res.sort(key=lambda r: (-r[0], r[2]['alias']))
        return res[:top] if top else res

    # ------------------------------------------------------------ split / merge / condense
    def _subset(self, system_ids, lesson_ids, origin):
        meta = {'schema_version': SCHEMA_VERSION, 'created': now(), 'updated': now(), 'profiles': {},
                'origin': [origin]}
        return KB({'kb_meta': meta,
                   'systems': {i: copy.deepcopy(self.systems[i]) for i in sorted(system_ids)},
                   'lessons': {i: copy.deepcopy(self.lessons[i]) for i in sorted(lesson_ids)}},
                  profiles=self.profiles)

    def split(self, by, include_general=True):
        '''{shard_name: KB}. Every system and lesson lands in at least one shard, so merging all shards
        reproduces this KB. by: profile | system | tag (tag family = text before ":") | category'''
        origin = {'split_from': self.path, 'by': by, 'date': now()}
        groups = {}
        if by in ('profile', 'system', 'tag'):
            for s in self.systems.values():
                if by == 'profile':
                    keys = [s['profile']]
                elif by == 'system':
                    keys = [f"{s['alias']}__{s['id']}"]
                else:
                    keys = sorted({t.split(':', 1)[0] for t in s.get('tags', [])}) or ['_untagged']
                for k in keys:
                    groups.setdefault(_safe(k), [set(), set()])[0].add(s['id'])
            placed = set()
            for name, (sids, lids) in groups.items():
                for l in self.lessons.values():
                    if l['scope'] == 'general' and not include_general:
                        continue
                    if any(self.applies(l, self.systems[i]) for i in sids):
                        lids.add(l['id']); placed.add(l['id'])
            rest = set(self.lessons) - placed
            gen = {i for i in rest if self.lessons[i]['scope'] == 'general'}
            if gen:
                groups['_general'] = [set(), gen]
            if rest - gen:
                groups['_unassigned'] = [set(), rest - gen]
        elif by == 'category':
            for l in self.lessons.values():
                groups.setdefault(_safe(l['category']), [set(), set()])[1].add(l['id'])
            if self.systems:
                groups['_systems'] = [set(self.systems), set()]
        else:
            raise KBError(f'split --by must be profile, system, tag or category, not {by!r}')
        return {name: self._subset(sids, lids, dict(origin, shard=name)) for name, (sids, lids) in groups.items()}

    @classmethod
    def merge(cls, kbs, prefer='newer', profiles=None):
        '''union of several KBs by id; returns (KB, conflicts). prefer: newer (by record "updated") | first | last'''
        if prefer not in ('newer', 'first', 'last'):
            raise KBError('prefer must be newer, first or last')
        out = cls(profiles=profiles if profiles is not None else (kbs[0].profiles if kbs else None))
        conflicts = []
        for kb in kbs:
            for kind in ('systems', 'lessons'):
                for i, rec in kb.data[kind].items():
                    if i in out.data[kind]:
                        a = out.data[kind][i]
                        c = []
                        out.data[kind][i] = _merge_val(a, rec, [kind, i], c, _prefer_b(a, rec, prefer))
                        conflicts += c
                    else:
                        out.data[kind][i] = copy.deepcopy(rec)
            for cid, c in kb.conflicts.items():
                a = out.conflicts.get(cid)
                out.conflicts[cid] = copy.deepcopy(c if a is None or c.get('updated', '') >= a.get('updated', '') else a)
            src = kb.path or (kb.meta.get('origin') or [None])[0]
            out.meta['origin'].append({'merged_from': src, 'date': now()})
        return out, conflicts

    def condense(self, query, min_score=0.5, top=None, include_general=True):
        '''focused KB for a known target: systems similar to query (same profile) above min_score, the lessons
        applying to them, the target's profile lessons, family lessons matching the query tags, general lessons'''
        hits = self.similar(query, top=top, min_score=min_score)
        sids = {s['id'] for _, _, s in hits}
        if query.get('id') in self.systems:
            sids.add(query['id'])
        lids = set()
        for l in self.lessons.values():
            if l['scope'] == 'general' and not include_general:
                continue
            if self.applies(l, query) or any(self.applies(l, self.systems[i]) for i in sids):
                lids.add(l['id'])
        return self._subset(sids, lids, {'condensed_from': self.path, 'query': query.get('id') or query.get('canonical'),
                                         'min_score': min_score, 'top': top, 'date': now()})

    # ------------------------------------------------------------ maintenance
    def rehash(self, refingerprint=True):
        '''recompute identity/canonical/id (and fingerprints whose inputs still exist) with the current profile
        versions; returns {old_id: new_id}. Systems that collapse onto one id are merged (conflicts reported).'''
        self._guard('rehash')
        mapping, new, conflicts = {}, {}, []
        for sid, s in self.systems.items():
            prof = self.profiles[s['profile']]
            ident = prof.normalize_identity(s['identity'])
            nid = prof.system_id(ident)
            s.update(identity=ident, canonical=prof.canonical(ident), id=nid, alias=prof.alias(ident),
                     profile_version=prof.version)
            if refingerprint and s.get('fingerprint_inputs'):
                try:
                    s['fingerprint'] = prof.fingerprint(ident, s['fingerprint_inputs'])
                except (OSError, ValueError):
                    pass
            if nid != sid:
                mapping[sid] = nid
            if nid in new:
                new[nid] = _merge_val(new[nid], s, ['systems', nid], conflicts, True)
            else:
                new[nid] = s
        self.data['systems'] = new
        for l in self.lessons.values():
            at = l.get('applies_to') or {}
            if any(i in mapping for i in at.get('systems', [])):
                at['systems'] = sorted({mapping.get(i, i) for i in at['systems']}); l['updated'] = now()
        if mapping:
            self.meta.setdefault('rehash_log', []).append({'date': now(), 'mapping': mapping})
        return mapping, conflicts

    # ------------------------------------------------------------ report
    def render(self):
        L = ['# Lessons-learned knowledge base (generated by `kbcli.py render`; do not edit)', '',
             f"Updated {self.meta.get('updated')}; {len(self.systems)} systems, {len(self.lessons)} lessons "
             f"({sum(l['status'] == 'active' for l in self.lessons.values())} active).", '']
        for pname in sorted({s['profile'] for s in self.systems.values()}):
            L += [f'## Systems: profile `{pname}`', '', '| alias | id | review | models | best | tags |', '|---|---|---|---|---|---|']
            for s in sorted((s for s in self.systems.values() if s['profile'] == pname), key=lambda s: s['alias']):
                L.append(f"| {s['alias']} | `{s['id']}` | {s['review']['level']} | {len(s['models'])} | "
                         f"{s.get('best_model') or '—'} | {', '.join(s.get('tags', []))} |")
            L.append('')
        L += ['## Lessons', '']
        for sc in SCOPES:
            ls = [l for l in self.lessons_for(scope=sc)]
            if not ls:
                continue
            L += [f'### Scope: {sc}', '']
            for l in ls:
                at = l['applies_to']
                who = '; '.join(f"{k}: {', '.join(self.systems[x]['alias'] if k == 'systems' and x in self.systems else x for x in v)}"
                                for k, v in at.items() if v)
                flags = ', '.join(f for f, on in (('seeded from docs', l.get('seeded_from_docs')),
                                                  ('not reviewed', not l.get('reviewed'))) if on)
                L.append(f"- **{l['title']}** (`{l['id']}`, {l['category']}, {l['confidence']}, {l['source']}"
                         f"{', ' + flags if flags else ''}){' — ' + who if who else ''}")
                L.append(f"  {l['text']}")
                L.append('  Evidence: ' + '; '.join(f"`{e['path']}`" + (f":{e['lines']}" if e.get('lines') else '')
                                                   + (f" ({e['note']})" if e.get('note') else '') for e in l['evidence']))
                if l.get('coverage'):
                    L.append('  Coverage: ' + '; '.join(l['coverage']))
                if l.get('verification'):
                    v = l['verification'][-1]
                    L.append(f"  Verification: {v['verdict'].replace('_', ' ')} ({v['date']}, {v['method']}"
                             f"{', ' + v['by'] if v.get('by') else ''}){': ' + v['note'] if v.get('note') else ''}")
                name = lambda i: f"{self.lessons[i]['title']} (`{i}`)" if i in self.lessons else f'`{i}`'
                if l.get('references'):
                    L.append('  References: ' + '; '.join(f"{r['rel']} {name(r['id'])}" for r in l['references']))
                if l.get('referenced_by'):
                    L.append('  Referenced by: ' + '; '.join(f"{r['rel']} {name(r['id'])}" for r in l['referenced_by']))
            L.append('')
        opn = sorted((c for c in self.conflicts.values() if c.get('status') == 'open'), key=lambda c: c['date'])
        if opn:
            L += ['## Open conflicts', '', 'Resolved only in reconciliation sessions (GUIDE §12).', '']
            for c in opn:
                head = (c.get('finding') or {}).get('title') or 'Lessons that contradict each other'
                L.append(f"- **{c['id']}** ({c.get('kind', 'finding')}, {c['date']}): {head}")
                L.append(f"  {c['disagreement']}")
                L.append('  Contradicts: ' + '; '.join(f"{self.lessons[x]['title']} (`{x}`)" if x in self.lessons else f'`{x}`'
                                                        for x in c['contradicts']))
            L.append('')
        sup = [l for l in self.lessons.values() if l['status'] == 'superseded']
        if sup:
            L += ['## Superseded lessons', ''] + [f"- ~~{l['title']}~~ (`{l['id']}` → " + ', '.join(f'`{x}`' for x in successors(l)) + ')' for l in sup] + ['']
        return '\n'.join(L) + '\n'


# ---------------------------------------------------------------- locked read-modify-write
@contextlib.contextmanager
def transaction(path, profiles=None, create=False, mode='general'):
    '''lock, load the current KB, yield it, then validate and save (atomic per file, with backups).
    path is a KB file (lock <path>.lock) or a shard directory (lock <dir>/.lock; records are routed to shards by
    save_shards). Any exception (including a validation failure) leaves the files untouched.
    mode 'general' (default) lets the KB only grow: existing lessons can't be edited, superseded, moved or
    replaced, and filled system/model fields can't be overwritten. 'reconcile' is for reconciliation sessions.'''
    if mode not in MODES:
        raise KBError(f'mode must be one of {MODES}')
    if isinstance(path, (list, tuple)):
        srcs = [(r, os.path.abspath(p)) for r, p in path]
        locks = []
        try:
            for p in sorted({p for _, p in srcs}):
                if create and not os.path.exists(p) and not p.endswith('.json'):
                    os.makedirs(p)
                f = open(os.path.join(p, '.lock') if os.path.isdir(p) else p + '.lock', 'a')
                fcntl.flock(f, fcntl.LOCK_EX); locks.append(f)
            for r, p in srcs:
                if create and os.path.isdir(p) and not _shard_files(p):
                    KB(profiles=profiles).save_shards(p, backup=False)
                elif create and not os.path.exists(p):
                    KB(path=p, profiles=profiles).save(p, backup=False)
            kb = KB.load_sources(srcs, profiles)
            kb.mode = mode
            yield kb
            kb.check()
            E, _ = kb.check_sources()
            if E:
                raise KBError('validation failed:\n  ' + '\n  '.join(E))
            kb.save_sources()
        finally:
            for f in locks:
                fcntl.flock(f, fcntl.LOCK_UN); f.close()
        return
    path = os.path.abspath(path)
    if os.path.isdir(path):
        with open(os.path.join(path, '.lock'), 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                files = _shard_files(path)
                if files:
                    kb = KB.load(path, profiles)
                elif create:
                    kb = KB(profiles=profiles)
                else:
                    raise KBError(f'{path} has no shards (create them with `kbcli.py init`)')
                kb.mode = mode
                yield kb
                kb.check()
                kb.save_shards(path)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
        return
    with open(path + '.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if os.path.exists(path):
                kb = KB.load(path, profiles)
            elif create:
                kb = KB(path=path, profiles=profiles)
            else:
                raise KBError(f'{path} does not exist (create it with `kbcli.py init`)')
            kb.mode = mode
            yield kb
            kb.check()
            kb.save(path)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


# ---------------------------------------------------------------- calibration
def resolve_inputs(inputs, base):
    out = {}
    for k, v in (inputs or {}).items():
        if isinstance(v, str) and not os.path.isabs(v) and os.path.exists(os.path.join(base, v)):
            v = os.path.join(base, v)
        out[k] = v
    return out


def check_calibration(profile, kb=None):
    '''evaluate profile/calibration.json; returns [(description, ok, detail)]. ok is None (skipped, detail
    "item not resolvable") when a check uses a KB system that is not in kb, e.g. before seeding.'''
    cal = profile.calibration()
    if not cal:
        return []
    items = {}
    for name, spec in cal.get('items', {}).items():
        if 'system' in spec:
            hits = kb.find(spec['system']) if kb else []
            if len(hits) != 1:
                items[name] = None; continue
            items[name] = hits[0]
        else:
            items[name] = build_system(profile, spec['identity'], resolve_inputs(spec.get('inputs'), profile.directory),
                                       partial=spec.get('partial', False), tags=spec.get('tags', []))
    th = cal.get('thresholds', {'similar': 0.7, 'dissimilar': 0.5})
    out = []
    for p in cal.get('pairs', []):
        a, b = items.get(p['a']), items.get(p['b'])
        if a is None or b is None:
            out.append((f"pair {p['a']} ~ {p['b']}", None, 'item not resolvable')); continue
        s, _ = profile.similarity(a, b)
        ok = s >= th['similar'] if p['expect'] == 'similar' else s < th['dissimilar']
        out.append((f"pair {p['a']} {'~' if p['expect'] == 'similar' else '!~'} {p['b']}", ok, round(s, 3)))
    for o in cal.get('orderings', []):
        q, c, f = (items.get(o[k]) for k in ('query', 'closer', 'farther'))
        if None in (q, c, f):
            out.append((f"order {o['query']}: {o['closer']} > {o['farther']}", None, 'item not resolvable')); continue
        sc, sf = profile.similarity(q, c)[0], profile.similarity(q, f)[0]
        out.append((f"order {o['query']}: {o['closer']} > {o['farther']}", sc > sf, (round(sc, 3), round(sf, 3))))
    return out
