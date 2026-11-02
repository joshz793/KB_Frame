'''Profile base class and registry.

A profile defines, for one class of chemical systems: what makes two entries "the same system" (identity fields
and their normalizers -> canonical key -> system id), what descriptors are stored for similarity (fingerprint),
how similarity is scored (named components with weights), and the vocabulary used to tag systems and lessons.
Profiles live in <kb root>/profiles/<name>/profile.py and must define a module-level PROFILE instance.
Extra profile directories can be listed in $LESSONS_KB_PROFILES (os.pathsep-separated).'''
import hashlib
import importlib.util
import json
import os

BUILTIN_PROFILES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'profiles')
_EXTRA_DIRS = []                  # profile directories from the project's config (lessons_kb.config)


class Field:
    '''One identity field.
    kind: 'str' | 'int' | 'float' | 'dict' | 'list'
    normalize: callable(value) -> canonical value (raise ValueError for invalid input)
    in_key: include in the canonical key (False = descriptive only, does not change the system id)'''

    def __init__(self, name, kind='str', required=True, default=None, normalize=None, in_key=True, doc=''):
        self.name, self.kind, self.required, self.default = name, kind, required, default
        self.normalize, self.in_key, self.doc = normalize, in_key, doc

    _types = {'str': str, 'int': int, 'float': float, 'dict': dict, 'list': list}

    def coerce(self, value):
        if self.kind in ('int', 'float') and isinstance(value, str):
            value = self._types[self.kind](value)
        if self.kind == 'dict' and isinstance(value, str):
            value = json.loads(value)
        if self.kind == 'list' and isinstance(value, str):
            value = [v for v in value.split(',') if v]
        if not isinstance(value, self._types[self.kind]) or (self.kind == 'int' and isinstance(value, bool)):
            raise ValueError(f'identity field {self.name!r} must be {self.kind}, got {value!r}')
        return self.normalize(value) if self.normalize else value


def render_value(v):
    if isinstance(v, dict):
        return ','.join(f'{k}:{render_value(v[k])}' for k in sorted(v))
    if isinstance(v, (list, tuple)):
        return ','.join(render_value(x) for x in v)
    return str(v)


def sha12(s):
    return hashlib.sha256(s.encode()).hexdigest()[:12]


class Profile:
    name = None
    version = '1'
    description = ''
    fields = []              # list of Field, in canonical-key order
    components = {}          # similarity component name -> weight; implemented as sim_<name>(a, b) -> float|None
    # value used for a component that one record cannot supply (None). Dropping such components (missing_value =
    # None) judges sparse records on the remaining, usually lenient, components and lets them outrank fully
    # described ones; a neutral value keeps the evidence weight comparable. Profiles may override it.
    missing_value = 0.5
    # components that are None for *every* candidate of a query (e.g. tags of an untagged query) are still dropped:
    # they cannot change the ranking, and dropping them keeps scores on the scale of the calibrated thresholds
    tag_vocabulary = {}      # tag or tag prefix ("family:" form allowed) -> meaning; empty = free tags
    strict_tags = False      # True: tags must be in tag_vocabulary (exact, or "prefix:value" with a listed prefix)
    lesson_categories = {}   # extra lesson categories this profile adds -> meaning
    result_fields = {}       # expected keys of model result_summary -> meaning (unknown keys only warn)
    observables = {}         # what lessons of this class measure or compute -> meaning, for context.observables
    fingerprint_inputs = {}  # input name -> meaning (e.g. {'xyz': 'path to a geometry file'})

    # ------------------------------------------------------------ identity
    def normalize_identity(self, identity, partial=False):
        '''check and normalize an identity dict. partial=True (similarity queries) skips missing required fields'''
        identity = dict(identity or {})
        known = {f.name for f in self.fields}
        unknown = set(identity) - known
        if unknown:
            raise ValueError(f'profile {self.name}: unknown identity field(s) {sorted(unknown)}; known: {sorted(known)}')
        out = {}
        for f in self.fields:
            v = identity.get(f.name, f.default)
            if v is None or v == '':
                if f.required and not partial:
                    raise ValueError(f'profile {self.name}: identity field {f.name!r} is required ({f.doc})')
                continue
            out[f.name] = f.coerce(v)
        return out

    def canonical(self, identity):
        parts = [f'profile={self.name}']
        for f in self.fields:
            if f.in_key and f.name in identity:
                parts.append(f'{f.name}={render_value(identity[f.name])}')
        return ';'.join(parts)

    def system_id(self, identity):
        return sha12(self.canonical(identity))

    def alias(self, identity):
        '''short human-readable name; override for something nicer'''
        return self.canonical(identity).split(';', 1)[-1]

    # ------------------------------------------------------------ descriptors
    def fingerprint(self, identity, inputs):
        '''JSON-serializable descriptors from the (normalized) identity and inputs (e.g. {'xyz': path}).
        Must be invariant to anything that does not change the system (orientation, atom order, units of input
        that the profile normalizes). Default: no descriptors.'''
        return {}

    def suggest_tags(self, identity, fingerprint):
        return []

    def check_tags(self, tags):
        if not self.strict_tags:
            return []
        bad = [t for t in tags if t not in self.tag_vocabulary and
               not (':' in t and t.split(':', 1)[0] + ':' in self.tag_vocabulary)]
        return [f'tag {t!r} not in the {self.name} tag vocabulary' for t in bad]

    # ------------------------------------------------------------ similarity
    def similarity(self, a, b):
        '''weighted mean of the components. A component returning None (data missing on one side) counts as
        missing_value, or is dropped with the weights renormalized when missing_value is None or when the query
        side lacks it (see _query_lacks). Returns (score, {component: (value, weight)}).'''
        parts, num, den = {}, 0.0, 0.0
        for name, w in self.components.items():
            v = getattr(self, f'sim_{name}')(a, b)
            parts[name] = (None if v is None else round(float(v), 4), w)
            if v is None:
                if self.missing_value is None or self._query_lacks(name, a):
                    continue
                v = self.missing_value
            num += w * float(v); den += w
        return (num / den if den else 0.0), parts

    def _query_lacks(self, name, a):
        '''True when the query record a cannot supply component name at all (then it is None for every candidate
        and is dropped). Tested by comparing a with itself.'''
        try:
            return getattr(self, f'sim_{name}')(a, a) is None
        except Exception:
            return True

    @staticmethod
    def tag_match(lesson_tag, system_tags):
        '''a lesson tag "t" matches a system tag "t" or "t:anything"'''
        return any(s == lesson_tag or s.startswith(lesson_tag + ':') for s in system_tags)

    def sim_tags(self, a, b):
        '''Jaccard on tags, counting a shared "prefix:" family as half a match. None (component dropped) when
        either side has no tags: an untagged query says nothing about tags.'''
        A, B = set(a.get('tags', [])), set(b.get('tags', []))
        if not A or not B:
            return None
        full = A & B
        pa = {t.split(':', 1)[0] for t in A - full if ':' in t}; pb = {t.split(':', 1)[0] for t in B - full if ':' in t}
        return (len(full) + 0.5 * len(pa & pb)) / len(A | B)

    # ------------------------------------------------------------ validation hooks
    def validate_system(self, system):
        '''extra profile checks on a full system record; returns (errors, warnings)'''
        warnings = []
        for m in system.get('models', []):
            extra = set(m.get('result_summary') or {}) - set(self.result_fields)
            if self.result_fields and extra:
                warnings.append(f"{system['id']} model {m.get('id')}: result_summary keys not in result_fields: {sorted(extra)}")
        return self.check_tags(system.get('tags', [])), warnings

    # ------------------------------------------------------------ calibration
    @property
    def directory(self):
        return getattr(self, '_dir', None)

    def calibration(self):
        '''calibration.json next to profile.py: {"thresholds": {"similar": x, "dissimilar": y},
        "items": {name: {"identity": {...}, "inputs": {...}, "tags": [...]} | {"system": "<id or alias>"}},
        "pairs": [{"a": name, "b": name, "expect": "similar"|"dissimilar"}],
        "orderings": [{"query": name, "closer": name, "farther": name}]}. Relative input paths are resolved
        against the profile directory.'''
        p = os.path.join(self.directory or '', 'calibration.json')
        if not os.path.exists(p):
            return None
        with open(p) as f:
            return json.load(f)

    def info(self):
        return {'name': self.name, 'version': self.version, 'description': self.description,
                'identity_fields': [{'name': f.name, 'kind': f.kind, 'required': f.required, 'in_key': f.in_key,
                                     'default': f.default, 'doc': f.doc} for f in self.fields],
                'fingerprint_inputs': self.fingerprint_inputs, 'components': self.components,
                'tag_vocabulary': self.tag_vocabulary, 'strict_tags': self.strict_tags,
                'lesson_categories': self.lesson_categories, 'result_fields': self.result_fields,
                'observables': self.observables,
                'directory': self.directory}


_REGISTRY = None


def profile_dirs():
    '''the built-in profiles, then the project's (config "profiles"), then $LESSONS_KB_PROFILES'''
    dirs = [BUILTIN_PROFILES] + list(_EXTRA_DIRS)
    dirs += [d for d in os.environ.get('LESSONS_KB_PROFILES', '').split(os.pathsep) if d]
    return dirs


def add_profile_dirs(dirs):
    '''make the profiles in dirs (each holding <name>/profile.py) available; resets the registry'''
    global _REGISTRY
    for d in dirs:
        d = os.path.abspath(d)
        if d not in _EXTRA_DIRS:
            _EXTRA_DIRS.append(d)
    _REGISTRY = None


def load_profiles(reload=False):
    '''{name: Profile instance} for every <dir>/<name>/profile.py defining PROFILE'''
    global _REGISTRY
    if _REGISTRY is not None and not reload:
        return _REGISTRY
    reg = {}
    for base in profile_dirs():
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            path = os.path.join(base, name, 'profile.py')
            if not os.path.isfile(path):
                continue
            spec = importlib.util.spec_from_file_location(f'kb_profile_{name}', path)
            mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
            prof = getattr(mod, 'PROFILE', None)
            if not isinstance(prof, Profile):
                raise TypeError(f'{path} must define PROFILE = <Profile subclass instance>')
            if prof.name != name:
                raise ValueError(f'{path}: PROFILE.name {prof.name!r} must equal its directory name {name!r}')
            prof._dir = os.path.dirname(path)
            reg[name] = prof
    _REGISTRY = reg
    return reg
