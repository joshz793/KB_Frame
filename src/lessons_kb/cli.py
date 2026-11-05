#!/usr/bin/env python3
'''kbcli: command-line interface to a lessons-learned knowledge base. Run `kbcli -h`, or see docs/GUIDE.md.
Where the KB is: the project's lessons-kb.json, found by searching upward from the working directory (or
$LESSONS_KB_CONFIG). Overrides: shared KB --db PATH or $LESSONS_KB; project KBs --project PATH (repeatable) or
$LESSONS_KB_PROJECT (':'-separated); --no-project reads the shared KB alone. `kbcli init` creates a KB here.'''
import argparse
import contextlib
import io
import json
import os
import sys

from lessons_kb.core import (KB, KBError, build_system, check_calibration, transaction, setup_match,  # noqa: E402
                     CORE_CATEGORIES, SETUP_KEYS, SHARED, PROJECT, RESOLUTION_ACTIONS, context_ok, successors,
                     EVIDENCE_KINDS, VERDICTS, VERIFY_METHODS)
from lessons_kb.profile import load_profiles  # noqa: E402
from lessons_kb import supplements as supp  # noqa: E402
from lessons_kb.config import find_config, load_config, write_config, CONFIG_NAME  # noqa: E402
from lessons_kb.profile import add_profile_dirs  # noqa: E402

CFG = None                        # the project's lessons-kb.json, loaded by main()


def kv(items, what):
    out = {}
    for it in items or []:
        if '=' not in it:
            raise KBError(f'{what} {it!r} must be key=value')
        k, v = it.split('=', 1)
        out[k] = v
    return out


def read_json(path):
    if path == '-':
        return json.load(sys.stdin)
    with open(path) as f:
        return json.load(f)


def emit(args, obj, text=None):
    if args.json or text is None:
        print(json.dumps(obj, indent=1, ensure_ascii=False, default=str))
    else:
        print(text)


def note_related(kb, lessons, exclude=()):
    '''remind the writer of related active lessons (stderr, so stdout stays machine-readable)'''
    for l in lessons:
        rel = kb.related_lessons(l, exclude=set(exclude) | {x['id'] for x in lessons if x.get('id')})
        if rel:
            print(f"related active lessons for {l.get('id') or l['title'][:60]}: does the new text contradict or make "
                  'any of them stale? If so, supersede them now (GUIDE §4 rule 5).', file=sys.stderr)
            for sc, b in rel:
                print(f"  {sc:.2f}  {b['id']}  {b['title'][:90]}", file=sys.stderr)


def contest_note(kb, l):
    c = kb.contested(l['id'])
    return f" [contested: {', '.join(c)}]" if c else ''


WRITE_CMDS = ('init', 'add-system', 'update-system', 'add-model', 'add-lesson', 'update-lesson', 'evidence', 'verify',
              'supersede', 'move', 'remove', 'conflict', 'supplement', 'rehash')


def db_path(args):
    p = args.db or os.environ.get('LESSONS_KB') or (CFG or {}).get('shared')
    if not p:
        raise KBError(f'no KB found: no {CONFIG_NAME} in this directory or above it. Create one with `kbcli init`, '
                      'or give --db')
    return p


def supplement_roots(args, db, kb):
    '''([(folder, ids of the lessons stored with it)], the shared folder): one folder per kind of KB, project
    folders first (GUIDE §2). Without project KBs there is one folder, for every lesson.'''
    cfg = (CFG or {}).get('supplements') or {} if CFG and not args.db and not os.environ.get('LESSONS_KB') else {}
    shared = cfg.get('shared') or supp.supplements_dir(db)
    if kb.sources is None:
        return [(shared, sorted(kb.lessons))], shared
    groups = {}
    for p, part in kb.source_parts().items():
        if dict((q, r) for r, q in kb.sources)[p] == SHARED:
            continue
        root = cfg.get('project') or supp.project_dir(p)
        groups.setdefault(root, set()).update(i for i in part.lessons if kb.role_of('lessons', i) == PROJECT)
    groups.setdefault(shared, set()).update(i for i in kb.lessons if kb.role_of('lessons', i) == SHARED)
    order = sorted(groups, key=lambda r: r == shared)
    return [(r, sorted(groups[r])) for r in order], shared


def project_paths(args):
    '''project KBs read together with the shared one'''
    if args.no_project:
        return []
    if args.project:
        return args.project
    env = os.environ.get('LESSONS_KB_PROJECT')
    if env:
        return [p for p in env.split(os.pathsep) if p]
    if args.db or os.environ.get('LESSONS_KB') or not CFG:
        return []
    return [p for p in CFG['project'] if os.path.exists(p)]


def kb_target(args):
    '''a path for transaction()/KB.load(): the shared KB alone, or [(role, path)] with the project KBs'''
    db, proj = db_path(args), project_paths(args)
    return [(SHARED, db)] + [(PROJECT, p) for p in proj] if proj else db


def load_kb(target):
    return KB.load_sources(target) if isinstance(target, list) else KB.load(target)


def query_from_args(args, kb=None):
    '''a system record (full if it exists in the KB, else a partial query) from --system or profile args'''
    if getattr(args, 'system', None):
        hits = kb.find(args.system)
        if len(hits) != 1:
            raise KBError(f'--system {args.system!r}: {len(hits)} matches')
        return hits[0]
    if not args.profile:
        raise KBError('give --system ID or --profile NAME with identity/inputs')
    prof = load_profiles()[args.profile]
    inputs = kv(args.input, '--input')
    if args.xyz:
        inputs['xyz'] = os.path.abspath(args.xyz)
    q = build_system(prof, kv(args.i, '-i'), inputs, partial=True, tags=args.tag or [])
    try:                                              # complete identity: give it its id (it may be in the KB)
        q['id'] = prof.system_id(prof.normalize_identity(q['identity']))
    except ValueError:
        pass
    return q


def add_setup_args(p):
    p.add_argument('--software', action='append', metavar='NAME',
                   help='software you will use, e.g. pyscf, openmolcas:single_aniso (repeatable or comma-separated)')
    p.add_argument('--platform', action='append', metavar='NAME',
                   help='machine/filesystem/container you will run on, e.g. perlmutter, podman-hpc (repeatable)')


def setup_from_args(args):
    '''{"software": [...], "platform": [...]} from --software/--platform, else $LESSONS_KB_SOFTWARE/_PLATFORM'''
    out = {}
    for k in SETUP_KEYS:
        vals = getattr(args, k, None) or [os.environ.get(f'LESSONS_KB_{k.upper()}', '')]
        vals = [v.strip() for x in vals for v in x.split(',') if v.strip()]
        if vals:
            out[k] = vals
    return out


def setup_note(l):
    at = l.get('applies_to') or {}
    cond = '; '.join(f"{k}={','.join(at[k])}" for k in SETUP_KEYS if at.get(k))
    return f' {{setup: {cond}}}' if cond else ''


def add_query_args(p, system=True):
    if system:
        p.add_argument('--system', help='id or alias of a system in the KB')
    p.add_argument('--profile')
    p.add_argument('-i', action='append', metavar='FIELD=VALUE', help='identity field (repeatable)')
    p.add_argument('--xyz', help='geometry file (shortcut for --input xyz=FILE)')
    p.add_argument('--input', action='append', metavar='NAME=VALUE', help='fingerprint input (repeatable)')
    p.add_argument('--tag', action='append', help='tag to attach to the query (repeatable)')


def main(argv=None, config_start=None):
    '''config_start: where to start looking for lessons-kb.json (default: the working directory)'''
    global CFG
    cfg_path = find_config(config_start)
    CFG = load_config(cfg_path) if cfg_path else None
    if CFG:
        add_profile_dirs(CFG['profiles'])
    ap = argparse.ArgumentParser(prog='kbcli.py', description=__doc__)
    ap.add_argument('--db', help='shared KB: shard directory or single KB file (default: db/ next to kbcli.py)')
    ap.add_argument('--project', action='append', metavar='PATH',
                    help='project KB read with the shared one (repeatable; default: db_project/ if present)')
    ap.add_argument('--no-project', action='store_true', help='read the shared KB alone')
    ap.add_argument('--reconcile', action='store_true',
                    help='reconciliation session: allow changes to existing records (also $LESSONS_KB_MODE=reconcile; '
                         'GUIDE §12)')
    ap.add_argument('--json', action='store_true', help='machine-readable output')
    sp = ap.add_subparsers(dest='cmd'); sp.required = True       # (required= needs python >= 3.7)
    sp.add_parser('init', help='create an empty KB at --db (a path ending in .json makes a single file, else a shard directory)')
    p = sp.add_parser('profiles', help='list profiles, or show one'); p.add_argument('name', nargs='?')
    sp.add_parser('categories', help='list core lesson categories')
    p = sp.add_parser('key', help='canonical key and id of an identity'); add_query_args(p, system=False)
    p = sp.add_parser('fingerprint', help='compute a fingerprint'); add_query_args(p, system=False)
    p = sp.add_parser('lookup', help='find systems by id or alias/canonical substring'); p.add_argument('q')
    p = sp.add_parser('show', help='print a system or lesson record'); p.add_argument('id')
    p = sp.add_parser('similar', help='rank systems similar to a query, with their lessons'); add_query_args(p)
    p.add_argument('--top', type=int, default=5); p.add_argument('--min-score', type=float, default=0.0)
    p.add_argument('--no-lessons', action='store_true'); add_setup_args(p)
    p = sp.add_parser('lessons', help='list lessons')
    for o in ('--system', '--profile', '--tag', '--category', '--scope'):
        p.add_argument(o)
    p.add_argument('--all', action='store_true', help='include superseded'); add_setup_args(p)
    p = sp.add_parser('add-system', help='add system(s) from JSON {profile, identity, inputs, ...} (object or list)')
    p.add_argument('--into', choices=(SHARED, PROJECT), help='KB to store them in (default: the project KB)')
    p.add_argument('--json-file', '-f', required=True); p.add_argument('--update', action='store_true')
    p = sp.add_parser('update-system', help='set top-level fields of a system'); p.add_argument('id')
    p.add_argument('--json-file', '-f', required=True)
    p = sp.add_parser('add-model', help='add a model to a system'); p.add_argument('id')
    p.add_argument('--json-file', '-f', required=True); p.add_argument('--update', action='store_true')
    p = sp.add_parser('add-lesson', help='add lesson(s) from JSON (object or list)')
    p.add_argument('--json-file', '-f', required=True)
    p.add_argument('--into', choices=(SHARED, PROJECT), help='KB to store them in (default: routed, see GUIDE §2)')
    p = sp.add_parser('supplement', help='copy the files lessons cite into supplements/ and link them (GUIDE §2)')
    p.add_argument('ids', nargs='*', metavar='LID')
    g = p.add_mutually_exclusive_group()
    g.add_argument('--all', action='store_true', help='every lesson, each into the folder of the KB that stores it')
    g.add_argument('--shared', action='store_true', help='every lesson stored in the shared KB')
    g.add_argument('--project', dest='project_only', action='store_true', help='every lesson stored in a project KB')
    p.add_argument('--referenced', action='store_true',
                   help='also copy the files that cited documents name (scripts, tables), listed in the manifest')
    p.add_argument('--max-size', default='1M', help='skip larger files (default 1M; suffixes k, M)')
    p.add_argument('--path', help='with --lines: the one cited file to excerpt')
    p.add_argument('--lines', help='A-B: store only these lines of --path (for large outputs)')
    p = sp.add_parser('remove', help='remove lessons without a successor (reconciliation; archived in conflicts.json)')
    p.add_argument('ids', nargs='+', metavar='LID'); p.add_argument('--why', required=True)
    p.add_argument('--by', default='kb-reconciler')
    p = sp.add_parser('move', help='move a system or lesson to the shared or the project KB'); p.add_argument('id')
    p.add_argument('--to', required=True, choices=(SHARED, PROJECT))
    p = sp.add_parser('update-lesson', help='edit fields of a lesson (normal sessions: metadata only)'); p.add_argument('id')
    p.add_argument('--json-file', '-f', required=True)
    p.add_argument('--replace', action='store_true', help='replace applies_to instead of extending it (to narrow where a lesson applies)')
    p = sp.add_parser('evidence', help="annotate one of a lesson's evidence entries, or add one (metadata)")
    p.add_argument('id'); p.add_argument('--path', required=True, help='the cited file (an existing entry, or a new one)')
    p.add_argument('--index', type=int, help='which entry, when several cite the same path (0-based)')
    p.add_argument('--kind', choices=EVIDENCE_KINDS); p.add_argument('--lines', help='A-B: the lines the claim rests on')
    p.add_argument('--quote', help='verbatim text at those lines'); p.add_argument('--supports', help='which claim or number')
    p.add_argument('--model', help='<system id>/<model id> the file belongs to'); p.add_argument('--note')
    p = sp.add_parser('verify', help="record a check of a lesson's claims against its evidence (metadata)")
    p.add_argument('id'); p.add_argument('--verdict', required=True, choices=VERDICTS)
    p.add_argument('--method', default='agent', choices=VERIFY_METHODS); p.add_argument('--by', default='')
    p.add_argument('--note', default='')
    p = sp.add_parser('supersede', help='mark a lesson superseded by another, or by several when splitting it')
    p.add_argument('old'); p.add_argument('--by', required=True, nargs='+', metavar='NEW')
    p = sp.add_parser('related', help='active lessons that may overlap or conflict with a lesson or a draft')
    g = p.add_mutually_exclusive_group(required=True); g.add_argument('id', nargs='?')
    g.add_argument('--json-file', '-f', help='draft lesson JSON (object or list), not yet added')
    p.add_argument('--top', type=int, default=8)
    p = sp.add_parser('validate', help='check the KB'); p.add_argument('--strict', action='store_true')
    p = sp.add_parser('conflicts', help='list conflicts (GUIDE §12)'); p.add_argument('--open', action='store_true')
    p.add_argument('--lesson', help='only conflicts that involve this lesson')
    p = sp.add_parser('conflict', help='record, show or resolve a conflict (GUIDE §12)')
    cs = p.add_subparsers(dest='ccmd'); cs.required = True
    q = cs.add_parser('add', help='record a finding that contradicts existing lessons (any session)')
    q.add_argument('--json-file', '-f', help='the finding: a lesson draft with context (omit for an internal conflict)')
    q.add_argument('--context-file', help='internal conflict between existing lessons: its context JSON')
    q.add_argument('--contradicts', nargs='+', required=True, metavar='LID')
    q.add_argument('--why', required=True, help='what disagrees, in one or two sentences')
    q = cs.add_parser('show', help='print a conflict with the lessons it contradicts'); q.add_argument('cid')
    q = cs.add_parser('adopt', help="add a conflict's finding as a lesson (reconciliation)"); q.add_argument('cid')
    q.add_argument('--into', choices=(SHARED, PROJECT))
    q = cs.add_parser('resolve', help='close a conflict (reconciliation)'); q.add_argument('cid')
    q.add_argument('--action', required=True, choices=RESOLUTION_ACTIONS)
    q.add_argument('--result', nargs='*', default=[], metavar='LID', help='lessons that now carry the knowledge')
    q.add_argument('--replace', nargs='*', default=[], metavar='LID',
                   help='old lessons to remove and archive in the conflict (references move to --result)')
    q.add_argument('--map', nargs='+', metavar='OLD=NEW[,NEW]',
                   help='which new lessons take over each replaced one (needed when several replace several), or "auto"')
    q.add_argument('--note', default='')
    q.add_argument('--by', default='kb-reconciler', help='who resolved it (default kb-reconciler)')
    p = sp.add_parser('links', help="a lesson's connections both ways: references, supersession, conflicts")
    p.add_argument('id')
    p = sp.add_parser('render', help='write the human-readable digest'); p.add_argument('--out')
    p = sp.add_parser('split', help='write shards'); p.add_argument('--by', required=True)
    p.add_argument('--out', required=True); p.add_argument('--no-general', action='store_true')
    p = sp.add_parser('merge', help='merge KB files/shard dirs into one file'); p.add_argument('inputs', nargs='+')
    p.add_argument('--out', required=True); p.add_argument('--prefer', default='newer', choices=('newer', 'first', 'last'))
    p = sp.add_parser('condense', help='focused KB for a known target'); add_query_args(p)
    p.add_argument('--min-score', type=float, default=0.5); p.add_argument('--top', type=int)
    p.add_argument('--out', required=True); p.add_argument('--no-general', action='store_true')
    sp.add_parser('rehash', help='recompute ids after a profile version change')
    p = sp.add_parser('calibrate', help="run a profile's calibration checks"); p.add_argument('--profile', required=True)
    args = ap.parse_args(argv)
    c = args.cmd
    try:
        if c == 'init':
            db = args.db or 'db'
            if not args.db and not os.path.exists(CONFIG_NAME):
                write_config(os.getcwd(), shared=db)
                print(f'wrote {CONFIG_NAME} (shared KB: {db})', file=sys.stderr)
        elif c in ('profiles', 'categories', 'key', 'fingerprint', 'merge'):
            db = target = None                        # these need no KB
        elif c == 'calibrate' and not (args.db or os.environ.get('LESSONS_KB') or CFG):
            db = target = None                        # profile calibration without a KB
        else:
            db = db_path(args)
            target = kb_target(args)
        if c == 'init':
            if os.path.exists(db) and not (os.path.isdir(db) and not os.listdir(db)):
                raise KBError(f'{db} already exists')
            if not db.endswith('.json'):
                os.makedirs(db, exist_ok=True)
            with transaction(db, create=True):
                pass
            emit(args, {'created': db}, f'created {db}')
        elif c == 'profiles':
            reg = load_profiles()
            if args.name:
                emit(args, reg[args.name].info())
            else:
                emit(args, {n: p.info() for n, p in reg.items()},
                     '\n'.join(f'{n} v{p.version}: {p.description}' for n, p in reg.items()))
        elif c == 'categories':
            emit(args, CORE_CATEGORIES, '\n'.join(f'{k}: {v}' for k, v in CORE_CATEGORIES.items()))
        elif c in ('key', 'fingerprint'):
            prof = load_profiles()[args.profile]
            ident = prof.normalize_identity(kv(args.i, '-i'), partial=(c == 'fingerprint'))
            if c == 'key':
                emit(args, {'canonical': prof.canonical(ident), 'id': prof.system_id(ident), 'alias': prof.alias(ident)},
                     f'{prof.system_id(ident)}  {prof.canonical(ident)}')
            else:
                inputs = kv(args.input, '--input')
                if args.xyz:
                    inputs['xyz'] = os.path.abspath(args.xyz)
                emit(args, prof.fingerprint(ident, inputs))
        elif c in ('lookup', 'show', 'similar', 'lessons', 'validate', 'render', 'split', 'condense', 'calibrate',
                   'related', 'conflicts', 'links') or (c == 'conflict' and args.ccmd == 'show'):
            kb = load_kb(target) if (c != 'calibrate' or (db and os.path.exists(db))) else None
            if c == 'lookup':
                hits = kb.find(args.q)
                emit(args, [{'id': s['id'], 'alias': s['alias'], 'profile': s['profile']} for s in hits],
                     '\n'.join(f"{s['id']}  {s['alias']}  ({s['profile']})" for s in hits) or 'no match')
            elif c == 'show':
                rec = kb.lessons.get(args.id) or (kb.find(args.id)[0] if len(kb.find(args.id)) == 1 else None)
                if rec is None:
                    rec = kb.conflicts.get(args.id)
                if rec is None:
                    raise KBError(f'no unique system, lesson or conflict {args.id!r}')
                if rec.get('id') in kb.lessons and kb.contested(rec['id']):
                    print(f"note: contested by open conflict(s) {', '.join(kb.contested(rec['id']))} "
                          '(`kbcli.py conflict show ID`)', file=sys.stderr)
                emit(args, rec)
            elif c == 'similar':
                q = query_from_args(args, kb)
                hits = kb.similar(q, top=args.top, min_score=args.min_score, exclude_self=bool(args.system))
                lessons = {}
                setup = setup_from_args(args)
                if not args.no_lessons:
                    for l in kb.lessons_for(system=q, setup=setup):
                        lessons[l['id']] = l
                    for _, _, s in hits:
                        for l in kb.lessons_for(system=s, setup=setup):
                            lessons.setdefault(l['id'], l)
                out = {'query': {k: q[k] for k in ('id', 'alias', 'canonical', 'tags')},
                       'hits': [{'score': sc, 'id': s['id'], 'alias': s['alias'], 'breakdown': parts} for sc, parts, s in hits],
                       'setup': setup,
                       'lessons': [{'id': l['id'], 'scope': l['scope'], 'category': l['category'], 'title': l['title'],
                                    'setup_condition': {k: l['applies_to'][k] for k in SETUP_KEYS if l['applies_to'].get(k)},
                                    'setup_match': setup_match(l, setup), 'contested_by': kb.contested(l['id'])}
                                   for l in lessons.values()]}
                txt = [f"query: {q['alias']}  tags={q['tags']}"]
                for h in out['hits']:
                    br = ', '.join(f'{k}={v[0]}' for k, v in h['breakdown'].items())
                    txt.append(f"  {h['score']:.3f}  {h['alias']}  [{h['id']}]  ({br})")
                if lessons:
                    sure = [l for l in lessons.values() if setup_match(l, setup)]
                    cond = [l for l in lessons.values() if setup_match(l, setup) is None]
                    txt.append(f"applicable lessons ({len(sure)}; `kbcli.py show ID` for text):")
                    txt += [f"  {l['id']}  [{l['scope']}/{l['category']}] {l['title']}{setup_note(l)}{contest_note(kb, l)}" for l in sure]
                    if cond:
                        txt.append(f"lessons conditional on a setup you did not declare ({len(cond)}; "
                                   f"filter with --software/--platform):")
                        txt += [f"  {l['id']}  [{l['scope']}/{l['category']}] {l['title']}{setup_note(l)}{contest_note(kb, l)}" for l in cond]
                emit(args, out, '\n'.join(txt))
            elif c == 'lessons':
                sysrec = kb._system(args.system) if args.system else None
                ls = kb.lessons_for(system=sysrec, profile=args.profile, tag=args.tag, category=args.category,
                                    scope=args.scope, include_superseded=args.all, setup=setup_from_args(args))
                emit(args, ls, '\n'.join(f"{l['id']}  [{l['scope']}/{l['category']}/{l['confidence']}] {l['title']}"
                                         f"{setup_note(l)}{contest_note(kb, l)}" for l in ls) or 'no lessons')
            elif c == 'related':
                if args.id:
                    drafts = [kb._lesson(args.id)]
                else:
                    drafts = read_json(args.json_file); drafts = drafts if isinstance(drafts, list) else [drafts]
                out = [{'lesson': d.get('id') or d.get('title'),
                        'related': [{'score': sc, 'id': b['id'], 'title': b['title']}
                                    for sc, b in kb.related_lessons(d, top=args.top)]} for d in drafts]
                emit(args, out, '\n'.join(f"{o['lesson']}:\n" + ('\n'.join(f"  {r['score']:.2f}  {r['id']}  {r['title'][:90]}"
                                                                         for r in o['related']) or '  none')
                                          for o in out))
            elif c == 'links':
                l = kb._lesson(args.id)
                def name(i):
                    if i in kb.lessons:
                        return f"{i}  {kb.lessons[i]['title'][:90]}"
                    if i in kb.conflicts:
                        r = kb.conflicts[i]
                        return f"{i}  [{r['status']}] {(r.get('finding') or {}).get('title') or r['disagreement'][:80]}"
                    return i
                out_ = [{'id': r['id'], 'rel': r['rel']} for r in l.get('references') or []] + \
                       [{'id': x, 'rel': 'superseded_by'} for x in successors(l)]
                in_ = l.get('referenced_by') or []
                emit(args, {'lesson': args.id, 'references': out_, 'referenced_by': in_},
                     f"{args.id}  {l['title']}\n  points to:\n" +
                     ('\n'.join(f"    {r['rel']:13} {name(r['id'])}" for r in out_) or '    nothing') +
                     '\n  pointed to by:\n' +
                     ('\n'.join(f"    {r['rel']:13} {name(r['id'])}" for r in in_) or '    nothing'))
            elif c == 'conflicts':
                cs_ = [r for r in kb.conflicts.values() if (not args.open or r['status'] == 'open')
                       and (not args.lesson or args.lesson in r['contradicts'] or args.lesson in
                            (r.get('resolution') or {}).get('result_ids', []))]
                emit(args, cs_, '\n'.join(f"{r['id']}  [{r['status']}] "
                                          f"{(r.get('finding') or {}).get('title') or 'internal: ' + r['disagreement'][:60]}\n"
                                          f"      contradicts: " + '; '.join(
                                              f"{x} {kb.lessons[x]['title'][:60]}" if x in kb.lessons else x
                                              for x in r['contradicts']) for r in sorted(cs_, key=lambda r: r['date']))
                     or 'no conflicts')
            elif c == 'conflict':                     # show
                r = kb._conflict(args.cid)
                out = dict(r, contradicted_lessons={x: kb.lessons[x] for x in r['contradicts'] if x in kb.lessons})
                emit(args, out)
            elif c == 'validate':
                E, W = kb.validate(strict=args.strict)
                E2, W2 = kb.check_sources(); E += E2; W += W2
                E2, W2 = supp.check_roots(kb, *supplement_roots(args, db, kb)); E += E2; W += W2
                emit(args, {'errors': E, 'warnings': W},
                     '\n'.join([f'ERROR {e}' for e in E] + [f'warning {w}' for w in W] +
                               [f'{len(E)} errors, {len(W)} warnings; {len(kb.systems)} systems, {len(kb.lessons)} lessons; '
                                f"{sum(1 for r in kb.conflicts.values() if r['status'] == 'open')} open conflicts"]))
                return 1 if E else 0
            elif c == 'render':
                if args.out or kb.sources is None:    # one digest of everything read
                    outs = {args.out or os.path.join(os.path.dirname(os.path.abspath(db.rstrip('/'))), 'LESSONS.md'): kb}
                else:                                 # one digest per source, so the shared one holds shared records only
                    outs = {}
                    for p, part in kb.source_parts().items():
                        role = dict((q, r) for r, q in kb.sources)[p]
                        outs[os.path.join(os.path.dirname(p.rstrip('/')), 'LESSONS.md') if role == SHARED
                             else os.path.join(p, 'LESSONS.md') if os.path.isdir(p) else p[:-5] + '.LESSONS.md'] = part
                for out, part in outs.items():
                    with open(out, 'w') as f:
                        f.write(part.render())
                emit(args, {'written': sorted(outs)}, '\n'.join(f'wrote {o}' for o in sorted(outs)))
            elif c == 'split':
                os.makedirs(args.out, exist_ok=True)
                shards = kb.split(args.by, include_general=not args.no_general)
                for name, sub in shards.items():
                    sub.save(os.path.join(args.out, f'{name}.json'), backup=False)
                emit(args, {n: {'systems': len(s.systems), 'lessons': len(s.lessons)} for n, s in shards.items()},
                     '\n'.join(f'{n}.json: {len(s.systems)} systems, {len(s.lessons)} lessons' for n, s in shards.items()))
            elif c == 'condense':
                q = query_from_args(args, kb)
                sub = kb.condense(q, min_score=args.min_score, top=args.top, include_general=not args.no_general)
                sub.check()
                sub.save(args.out, backup=False)
                emit(args, {'written': args.out, 'systems': sorted(sub.systems), 'lessons': len(sub.lessons)},
                     f'wrote {args.out}: {len(sub.systems)} systems '
                     f"({', '.join(s['alias'] for s in sub.systems.values())}), {len(sub.lessons)} lessons")
            elif c == 'calibrate':
                prof = load_profiles()[args.profile]
                res = check_calibration(prof, kb)
                emit(args, [{'check': d, 'ok': ok, 'detail': det} for d, ok, det in res],
                     '\n'.join(f"{'SKIP' if ok is None else 'PASS' if ok else 'FAIL'}  {d}  {det}" for d, ok, det in res)
                     or 'no calibration.json')
                return 0 if all(ok is not False for _, ok, _ in res) else 1
        elif c == 'merge':
            kbs = [KB.load(p) for p in args.inputs]
            merged, conflicts = KB.merge(kbs, prefer=args.prefer)
            merged.check()
            merged.save(args.out, backup=os.path.exists(args.out))
            emit(args, {'written': args.out, 'conflicts': conflicts},
                 f'wrote {args.out}: {len(merged.systems)} systems, {len(merged.lessons)} lessons; {len(conflicts)} conflicts' +
                 ''.join(f"\n  conflict {x['path']}: {x['a']!r} vs {x['b']!r} (kept {x['kept']})" for x in conflicts))
        else:                                        # write commands: locked read-modify-write
            mode = 'reconcile' if args.reconcile or os.environ.get('LESSONS_KB_MODE') == 'reconcile' else 'general'
            out_buf, err_buf = io.StringIO(), io.StringIO()   # printed only once the write has committed
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf), \
                    transaction(target, mode=mode) as kb:
                if c == 'add-system':
                    recs = read_json(args.json_file); recs = recs if isinstance(recs, list) else [recs]
                    out = []
                    for r in recs:
                        r = dict(r); prof = r.pop('profile'); ident = r.pop('identity'); inputs = r.pop('inputs', {})
                        rec = build_system(prof, ident, inputs, **r)
                        conf = kb.add_system(rec, update=args.update)
                        if args.into and kb.sources is not None:
                            kb.place(rec['id'], args.into)
                        out.append({'id': rec['id'], 'alias': rec['alias'], 'conflicts': conf})
                    emit(args, out, '\n'.join(f"{o['id']}  {o['alias']}" + (f"  ({len(o['conflicts'])} fields replaced)" if o['conflicts'] else '') for o in out))
                elif c == 'update-system':
                    kb.update_system(args.id, read_json(args.json_file)); emit(args, {'updated': args.id}, f'updated {args.id}')
                elif c == 'add-model':
                    ms = read_json(args.json_file); ms = ms if isinstance(ms, list) else [ms]
                    for m in ms:
                        kb.add_model(args.id, m, update=args.update)
                    emit(args, {'system': args.id, 'models': [m['id'] for m in ms]}, f"{args.id}: {[m['id'] for m in ms]}")
                elif c == 'add-lesson':
                    ls = read_json(args.json_file); ls = ls if isinstance(ls, list) else [ls]
                    if mode == 'general':
                        for l in ls:
                            if not context_ok(l.get('context')):
                                raise KBError(f"lesson {l.get('title', '')[:60]!r} has no context; give "
                                              '{"context": {"systems": [...], "observables": [...], "methods": [...], '
                                              '"conditions": [...]}} (GUIDE §3)')
                    ids = [kb.add_lesson(l) for l in ls]
                    if kb.sources is not None:
                        for i in ids:
                            if args.into:
                                kb.place(i, args.into)
                        print('stored in: ' + ', '.join(f"{i} {kb.role_of('lessons', i)}" for i in ids), file=sys.stderr)
                    emit(args, ids, '\n'.join(ids))
                    note_related(kb, [kb.lessons[i] for i in ids])
                elif c == 'update-lesson':
                    kb.update_lesson(args.id, read_json(args.json_file), replace=args.replace)
                    emit(args, {'updated': args.id}, f'updated {args.id}')
                elif c == 'evidence':
                    patch = {k: getattr(args, k) for k in ('path', 'index', 'kind', 'lines', 'quote', 'supports', 'model', 'note')
                             if getattr(args, k) is not None}
                    patch['path'] = os.path.abspath(args.path) if not os.path.isabs(args.path) else args.path
                    kb.update_lesson(args.id, {'evidence': [patch]})
                    emit(args, {'updated': args.id, 'evidence': patch['path']}, f"updated {args.id}: evidence {patch['path']}")
                elif c == 'verify':
                    rec = {'verdict': args.verdict, 'method': args.method, 'by': args.by}
                    if args.note:
                        rec['note'] = args.note
                    kb.add_verification(args.id, rec)
                    emit(args, {'verified': args.id, 'verdict': args.verdict}, f'{args.id}: {args.verdict}')
                elif c == 'supersede':
                    kb.supersede(args.old, args.by)
                    emit(args, {'superseded': args.old, 'by': args.by}, f"{args.old} -> {', '.join(args.by)}")
                    note_related(kb, [kb.lessons[i] for i in args.by], exclude=[args.old])
                elif c == 'supplement':
                    size = args.max_size.strip()
                    mult = {'k': 1e3, 'K': 1e3, 'M': 1e6, 'G': 1e9}.get(size[-1:], 1)
                    max_size = int(float(size.rstrip('kKMG')) * mult)
                    roots, shared_root = supplement_roots(args, db, kb)
                    if args.ids and (args.all or args.shared or args.project_only):
                        raise KBError('give lesson ids, or one of --all, --shared, --project')
                    run = None
                    if args.shared or args.project_only:
                        run = {r for r, _ in roots if (r == shared_root) == bool(args.shared)}
                    lids = args.ids or [i for r, ids in roots if run is None or r in run for i in ids]
                    if not lids and not (args.all or args.shared or args.project_only):
                        raise KBError('give lesson ids, or one of --all, --shared, --project')
                    for i in lids:
                        kb._lesson(i)
                    excerpt = None
                    if args.lines:
                        if not args.path or len(lids) != 1:
                            raise KBError('--lines needs --path and exactly one lesson')
                        a, b = (int(x) for x in args.lines.split('-'))
                        excerpt = (lids[0], args.path, (a, b))
                    reps = supp.sync_roots(kb, roots, lids, run=run, max_size=max_size, referenced=args.referenced,
                                           excerpt=excerpt)
                    emit(args, reps, '\n'.join(
                        f"{root}: {len(rep['copied'])} files copied, {len(rep['moved'])} moved, "
                        f"{len(rep['removed'])} removed, {len(rep['linked'])} citations linked, "
                        f"{len(set(rep['referenced']))} referenced files; "
                        f"{len(rep['skipped_large'])} skipped as too large, {len(rep['missing'])} missing" +
                        ''.join(f"\n  too large: {x[1]} ({x[2] / 1e6:.1f} MB; {x[0]})" for x in rep['skipped_large']) +
                        ''.join(f"\n  missing: {x[1]} ({x[0]})" for x in rep['missing'])
                        for root, rep in reps.items()) or 'nothing to copy')
                elif c == 'conflict':
                    if args.ccmd == 'add':
                        if not args.json_file and not args.context_file:
                            raise KBError('give the finding (-f) or, for an internal conflict, --context-file')
                        cid = kb.add_conflict(read_json(args.json_file) if args.json_file else None, args.contradicts,
                                              args.why, context=read_json(args.context_file) if args.context_file else None)
                        role = kb.conflict_role(kb.conflicts[cid]) if kb.sources is not None else SHARED
                        emit(args, {'conflict': cid, 'stored_in': role}, f'{cid}  (stored in the {role} KB; '
                             'resolved in a reconciliation session, GUIDE §12)')
                    elif args.ccmd == 'adopt':
                        lid = kb.adopt_finding(args.cid)
                        if args.into and kb.sources is not None:
                            kb.place(lid, args.into)
                        emit(args, {'lesson': lid}, lid)
                    elif args.ccmd == 'resolve':
                        mapping = None
                        if args.map == ['auto']:
                            mapping = 'auto'
                        elif args.map:
                            mapping = {}
                            for m in args.map:
                                o, _, n = m.partition('=')
                                mapping[o] = [x for x in n.split(',') if x]
                        archived = kb.replace(args.replace, args.result, mapping) if args.replace else []
                        kb.resolve_conflict(args.cid, args.action, args.result, archived, args.note, by=args.by)
                        emit(args, {'resolved': args.cid, 'action': args.action, 'removed': args.replace},
                             f"{args.cid} {args.action}" + (f"; removed {', '.join(args.replace)}" if args.replace else ''))
                elif c == 'remove':
                    role = kb.role_of('lessons', args.ids[0]) if kb.sources is not None else SHARED
                    archived = kb.remove(args.ids)
                    cid = kb.record_direct('remove', args.ids, args.why, by=args.by, archived=archived)
                    kb.conflicts[cid]['role'] = role
                    emit(args, {'removed': args.ids, 'record': cid}, f"removed {', '.join(args.ids)}; archived in {cid}")
                elif c == 'move':
                    if kb.sources is None:
                        raise KBError('move needs a project KB (db_project/, --project or $LESSONS_KB_PROJECT)')
                    kb.place(args.id, args.to); emit(args, {'moved': args.id, 'to': args.to}, f'{args.id} -> {args.to}')
                elif c == 'rehash':
                    mapping, conflicts = kb.rehash()
                    emit(args, {'mapping': mapping, 'conflicts': conflicts},
                         f'{len(mapping)} ids changed' + ''.join(f'\n  {a} -> {b}' for a, b in mapping.items()))
            sys.stdout.write(out_buf.getvalue()); sys.stderr.write(err_buf.getvalue())
    except (KBError, ValueError, KeyError, OSError) as e:
        print(f'error: {e}' + ('' if c not in WRITE_CMDS else ' (nothing was written)'), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
