'''Where a project's KB lives: lessons-kb.json, found by searching upward from the working directory.

  {"shared": "db",                       the shared KB (a shard directory or a KB file)
   "project": ["db_project"],            project KBs read together with it (optional)
   "profiles": ["profiles"],             directories of the project's own profiles (optional)
   "supplements": "supplements"}         copies of cited files (optional; default: beside "shared")
Relative paths are taken from the config file's directory. $LESSONS_KB_CONFIG names a config file directly.
Command-line options and $LESSONS_KB / $LESSONS_KB_PROJECT override the config (GUIDE.md §5).'''
import json
import os

CONFIG_NAME = 'lessons-kb.json'
KEYS = ('shared', 'project', 'profiles', 'supplements')


def find_config(start=None):
    '''path of the config file that applies at start (default: the working directory), or None'''
    env = os.environ.get('LESSONS_KB_CONFIG')
    if env:
        return os.path.abspath(env)
    d = os.path.abspath(start or os.getcwd())
    while True:
        p = os.path.join(d, CONFIG_NAME)
        if os.path.isfile(p):
            return p
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up


def load_config(path):
    '''the config with absolute paths: {"shared": str|None, "project": [...], "profiles": [...], "supplements": str|None}'''
    with open(path) as f:
        raw = json.load(f)
    bad = set(raw) - set(KEYS)
    if bad:
        raise ValueError(f'{path}: unknown key(s) {sorted(bad)}; allowed {KEYS}')
    base = os.path.dirname(os.path.abspath(path))
    absp = lambda p: p if os.path.isabs(p) else os.path.normpath(os.path.join(base, p))
    listify = lambda v: [v] if isinstance(v, str) else list(v or [])
    return {'shared': absp(raw['shared']) if raw.get('shared') else None,
            'project': [absp(p) for p in listify(raw.get('project'))],
            'profiles': [absp(p) for p in listify(raw.get('profiles'))],
            'supplements': absp(raw['supplements']) if raw.get('supplements') else None,
            'path': os.path.abspath(path)}


def write_config(directory, shared='db', project=None):
    '''create lessons-kb.json in directory (refuses to overwrite)'''
    p = os.path.join(directory, CONFIG_NAME)
    if os.path.exists(p):
        raise FileExistsError(f'{p} already exists')
    cfg = {'shared': shared}
    if project:
        cfg['project'] = project
    with open(p, 'w') as f:
        f.write(json.dumps(cfg, indent=1) + '\n')
    return p
