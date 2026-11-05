'''Core KB tests (stdlib unittest; uses the generic profile). Run: python tests/test_core.py'''
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # the framework checkout
sys.path.insert(0, os.path.join(ROOT, 'src'))
from lessons_kb.core import KB, KBError, build_system, transaction  # noqa: E402
from lessons_kb.profile import load_profiles  # noqa: E402

EX = os.path.join(ROOT, 'src', 'lessons_kb', 'profiles', 'generic', 'examples')
GEN = load_profiles()['generic']
EV = [{'path': '/dev/null', 'note': 'test'}]
CTX = {'systems': ['test system'], 'observables': ['workflow']}


def read(p):
    with open(p) as f:
        return f.read()


def sample_kb(path=None):
    kb = KB(path=path)
    for f, m, x, extra in (('H2O', 1, 'water', {}), ('CH4', 1, 'methane', {}),
                           ('C2H6O', 1, 'ethanol', {'descriptors': {'isomer': 'ethanol'}}), ('O2', 3, None, {})):
        kb.add_system(build_system(GEN, dict(formula=f, multiplicity=m, **extra),
                                   {'xyz': os.path.join(EX, f'{x}.xyz')} if x else {}, tags=['test']))
    w = GEN.system_id({'formula': 'H2O', 'charge': 0, 'multiplicity': 1})
    kb.add_model(w, {'id': 'M1', 'status': 'done', 'level_of_theory': 'toy'})
    kb.add_lesson({'title': 'general', 'text': 'g', 'category': 'workflow', 'scope': 'general', 'evidence': EV})
    kb.add_lesson({'title': 'profile', 'text': 'p', 'category': 'method', 'scope': 'profile',
                   'applies_to': {'profiles': ['generic']}, 'evidence': EV})
    kb.add_lesson({'title': 'family', 'text': 'f', 'category': 'active_space', 'scope': 'family',
                   'applies_to': {'tags': ['open_shell']}, 'evidence': EV})
    kb.add_lesson({'title': 'orphan', 'text': 'o', 'category': 'basis', 'scope': 'family',
                   'applies_to': {'tags': ['nothing_has_this']}, 'evidence': EV})
    kb.add_lesson({'title': 'system', 'text': 's', 'category': 'analysis', 'scope': 'system',
                   'applies_to': {'systems': [w]}, 'evidence': EV})
    kb.check()
    return kb, w


class TestIds(unittest.TestCase):
    def test_stable_and_normalized(self):
        ident = GEN.normalize_identity({'formula': 'OH2', 'multiplicity': '1'})
        canon = 'profile=generic;formula=H2O;charge=0;multiplicity=1'
        self.assertEqual(GEN.canonical(ident), canon)
        self.assertEqual(GEN.system_id(ident), hashlib.sha256(canon.encode()).hexdigest()[:12])
        self.assertEqual(GEN.system_id(GEN.normalize_identity({'formula': 'H2O', 'multiplicity': 1})), GEN.system_id(ident))
        self.assertNotEqual(GEN.system_id(GEN.normalize_identity({'formula': 'H2O', 'multiplicity': 3})), GEN.system_id(ident))
        self.assertNotEqual(GEN.system_id(GEN.normalize_identity({'formula': 'H2O', 'charge': 1, 'multiplicity': 2})),
                            GEN.system_id(ident))
        # name is descriptive only
        self.assertEqual(GEN.system_id(GEN.normalize_identity({'formula': 'H2O', 'multiplicity': 1, 'name': 'water'})),
                         GEN.system_id(ident))

    def test_lesson_id_is_content(self):
        kb, _ = sample_kb()
        l = next(iter(kb.lessons.values()))
        with self.assertRaises(KBError):
            kb.update_lesson(l['id'], {'text': 'changed'})
        kb.update_lesson(l['id'], {'confidence': 'established', 'evidence': [{'path': '/tmp/x', 'note': 'more'}]})
        self.assertEqual(len(kb.lessons[l['id']]['evidence']), 2)
        kb.check()


class TestValidation(unittest.TestCase):
    def test_rejections(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, 'kb.json')
            kb, w = sample_kb(); kb.save(db)
            before = read(db)
            with self.assertRaises(KBError):                       # lesson without evidence
                with transaction(db) as k:
                    k.add_lesson({'title': 't', 'text': 'x', 'category': 'method', 'scope': 'general', 'evidence': []})
            with self.assertRaises(KBError):                       # duplicate system
                with transaction(db) as k:
                    k.add_system(build_system(GEN, {'formula': 'H2O', 'multiplicity': 1}))
            with self.assertRaises(KBError):                       # unknown lesson category for its profiles
                with transaction(db) as k:
                    k.add_lesson({'title': 't', 'text': 'x', 'category': 'nonsense', 'scope': 'general', 'evidence': EV})
            self.assertEqual(read(db), before, 'failed transactions must leave the file untouched')
            bad = copy.deepcopy(kb.data); bad['systems'][w]['profile'] = 'no_such_profile'
            E, _ = KB(bad).validate()
            self.assertTrue(any('unknown profile' in e for e in E))
            bad = copy.deepcopy(kb.data); bad['systems'][w]['identity']['formula'] = 'OH2'
            self.assertTrue(KB(bad).validate()[0])                 # not normalized
            bad = copy.deepcopy(kb.data); next(iter(bad['lessons'].values()))['text'] = 'edited'
            self.assertTrue(any('content' in e for e in KB(bad).validate()[0]))

    def test_dir_load_is_readonly(self):
        with tempfile.TemporaryDirectory() as d:
            kb, _ = sample_kb(); kb.save(os.path.join(d, 'a.json'))
            k2 = KB.load(d)
            with self.assertRaises(KBError):
                k2.save()


class TestConcurrency(unittest.TestCase):
    def test_two_writers(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, 'kb.json')
            subprocess.run([sys.executable, os.path.join(ROOT, 'kbcli.py'), '--db', db, 'init'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            script = ('import sys, json, subprocess\n'
                      'for i in range(5):\n'
                      '    l = {"title": f"w{sys.argv[1]}-{i}", "text": "t", "category": "workflow", "scope": "general",\n'
                      '         "evidence": [{"path": "/dev/null"}], "context": {"observables": ["workflow"]}}\n'
                      f'    p = "{d}/l" + sys.argv[1] + str(i) + ".json"; json.dump(l, open(p, "w"))\n'
                      f'    subprocess.run([sys.executable, "{ROOT}/kbcli.py", "--db", "{db}", "add-lesson", "-f", p], check=True,\n'
                      '                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)\n')
            procs = [subprocess.Popen([sys.executable, '-c', script, str(k)]) for k in (1, 2)]
            for p in procs:
                self.assertEqual(p.wait(), 0)
            self.assertEqual(len(KB.load(db).lessons), 10)


class TestSplitMerge(unittest.TestCase):
    def test_roundtrip(self):
        kb, _ = sample_kb()
        for by in ('profile', 'system', 'tag', 'category'):
            for gen in (True, False):
                with tempfile.TemporaryDirectory() as d:
                    for name, sub in kb.split(by, include_general=gen).items():
                        sub.check()
                        sub.save(os.path.join(d, f'{name}.json'), backup=False)
                    back = KB.load(d)
                    self.assertEqual(back.systems, kb.systems, by)
                    self.assertEqual(back.lessons, kb.lessons, by)

    def test_conflicts_and_prefer(self):
        kb, w = sample_kb()
        a = copy.deepcopy(kb); b = copy.deepcopy(kb)
        a.systems[w]['notes'] = 'old'; a.systems[w]['updated'] = '2026-01-01T00:00:00'
        b.systems[w]['notes'] = 'new'; b.systems[w]['updated'] = '2026-02-01T00:00:00'
        m, conf = KB.merge([a, b], prefer='newer')
        self.assertEqual(m.systems[w]['notes'], 'new'); self.assertEqual(len(conf), 1)
        self.assertEqual(KB.merge([a, b], prefer='first')[0].systems[w]['notes'], 'old')
        self.assertEqual(KB.merge([b, a], prefer='newer')[0].systems[w]['notes'], 'new')
        # models are unioned by id
        b.systems[w]['models'].append({'id': 'M2', 'status': 'pending'})
        self.assertEqual([x['id'] for x in KB.merge([a, b])[0].systems[w]['models']], ['M1', 'M2'])

    def test_condense_merges_back(self):
        kb, w = sample_kb()
        sub = kb.condense(kb.systems[w], min_score=0.8)
        sub.check()
        self.assertIn(w, sub.systems)
        titles = {l['title'] for l in sub.lessons.values()}
        self.assertTrue({'general', 'profile', 'system'} <= titles and 'family' not in titles, titles)
        m, conf = KB.merge([kb, sub])
        self.assertEqual(conf, []); self.assertEqual(m.systems, kb.systems); self.assertEqual(m.lessons, kb.lessons)


class TestSupersede(unittest.TestCase):
    def test_split_and_merge(self):
        kb, w = sample_kb()
        old = kb.add_lesson({'title': 'broad and narrow', 'text': 'two statements', 'category': 'method',
                             'scope': 'system', 'applies_to': {'systems': [w]}, 'evidence': EV})
        a = kb.add_lesson({'title': 'broad', 'text': 'holds for the family', 'category': 'method', 'scope': 'family',
                           'applies_to': {'tags': ['test']}, 'evidence': EV})
        b = kb.add_lesson({'title': 'narrow', 'text': 'holds for water', 'category': 'method', 'scope': 'system',
                           'applies_to': {'systems': [w]}, 'evidence': EV})
        with self.assertRaises(KBError):
            kb.supersede(old, [old, a])
        with self.assertRaises(KBError):
            kb.supersede(old, [])
        before = copy.deepcopy(kb)
        kb.supersede(old, [a, b, a])
        self.assertEqual(kb.lessons[old]['superseded_by'], [a, b])
        kb.check(strict=True)
        self.assertIn(f'`{a}`, `{b}`', kb.render())
        self.assertNotIn(old, [l['id'] for l in kb.lessons_for(system=kb.systems[w])])
        # a single successor is stored as a plain id
        kb2 = copy.deepcopy(before); kb2.supersede(old, a)
        self.assertEqual(kb2.lessons[old]['superseded_by'], a)
        # merging a one-way and a split supersede unions the successors
        m, _ = KB.merge([kb2, kb])
        self.assertEqual(m.lessons[old]['superseded_by'], [a, b]); m.check(strict=True)
        # dangling successor
        bad = copy.deepcopy(kb.data); bad['lessons'][old]['superseded_by'] = [a, 'L-0000000000']
        self.assertTrue(KB(bad).validate(strict=True)[0])
        bad['lessons'][old]['superseded_by'] = 7
        self.assertTrue(KB(bad).validate()[0])

    def test_warnings(self):
        kb, w = sample_kb()
        long_ = kb.add_lesson({'title': 'long', 'text': 'x ' * 400, 'category': 'method', 'scope': 'general',
                               'evidence': EV})
        self.assertTrue(any(long_ in x and 'one statement' in x for x in kb.validate()[1]))
        seeded = kb.add_lesson({'title': 'seeded', 'text': 'Ghost functions on the caps overshoot the reference '
                                'shell in oxides. Validate them per host.', 'category': 'environment',
                                'scope': 'general', 'evidence': EV, 'seeded_from_docs': True})
        copy_ = kb.add_lesson({'title': 'seeded, extended', 'text': 'Ghost functions on the caps overshoot the '
                               'reference shell in oxides. New data agree.', 'category': 'environment',
                               'scope': 'general', 'evidence': EV})
        kb.supersede(seeded, copy_)
        self.assertTrue(any(copy_ in x and 'seeded' in x for x in kb.validate()[1]))
        kb.update_lesson(copy_, {'seeded_from_docs': True})
        self.assertFalse(any(copy_ in x and 'seeded' in x for x in kb.validate()[1]))

    def test_broad_tag_warning(self):
        kb, w = sample_kb()                         # every system is tagged "test"; give two of them "open_shell"
        for sid in list(kb.systems)[:2]:
            kb.systems[sid]['tags'] = kb.systems[sid]['tags'] + ['open_shell']
        lid = kb.add_lesson({'title': 'open shells', 'text': 'holds for open shells', 'category': 'method',
                             'scope': 'family', 'applies_to': {'tags': ['test', 'open_shell']}, 'evidence': EV})
        self.assertTrue(any(lid in x and "'test' already matches" in x for x in kb.validate()[1]))
        with self.assertRaises(KBError):
            kb.update_lesson(lid, {'applies_to': {'colour': []}}, replace=True)
        kb.update_lesson(lid, {'applies_to': {'tags': ['open_shell']}})          # extending cannot narrow
        self.assertEqual(kb.lessons[lid]['applies_to']['tags'], ['open_shell', 'test'])
        kb.update_lesson(lid, {'applies_to': {'tags': ['open_shell']}}, replace=True)
        self.assertEqual(kb.lessons[lid]['applies_to']['tags'], ['open_shell'])
        self.assertFalse(any(lid in x for x in kb.validate()[1]))
        kb.check(strict=True)
        # a tag and its own sub-tag: flagged without looking at systems
        l2 = kb.add_lesson({'title': 'ions', 'text': 'holds for ions', 'category': 'method', 'scope': 'family',
                            'applies_to': {'tags': ['ion', 'ion:Er3+']}, 'evidence': EV})
        self.assertTrue(any(l2 in x for x in kb.validate()[1]))

    def test_related(self):
        kb, w = sample_kb()
        a = kb.add_lesson({'title': 'Scaled cap pseudopotentials fit the wrong physics', 'text': 'Do not rescale cap '
                           'pseudopotentials to match a reference shell.', 'category': 'environment',
                           'scope': 'general', 'evidence': EV})
        draft = {'title': 'Do not tune cap pseudopotential strength', 'text': 'Caps keep standard pseudopotentials.',
                 'category': 'environment', 'scope': 'general'}
        rel = kb.related_lessons(draft)
        self.assertEqual(rel[0][1]['id'], a)
        kb.supersede(a, kb.add_lesson({'title': 'unrelated', 'text': 'zzz', 'category': 'workflow',
                                       'scope': 'general', 'evidence': EV}))
        self.assertNotIn(a, [b['id'] for _, b in kb.related_lessons(draft)])    # only active lessons

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, 'db')
            cli = [sys.executable, os.path.join(ROOT, 'kbcli.py'), '--db', db]
            subprocess.run(cli + ['init'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            ids = []
            for t in ('cap rescaling fits wrong physics', 'cap rescaling: broad part', 'cap rescaling: narrow part'):
                f = os.path.join(d, 'l.json')
                with open(f, 'w') as fh:
                    json.dump({'title': t, 'text': t, 'category': 'environment', 'scope': 'general', 'evidence': EV,
                               'context': CTX}, fh)
                r = subprocess.run(cli + ['add-lesson', '-f', f], check=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, universal_newlines=True)
                ids.append(r.stdout.strip())
            self.assertIn(ids[0], r.stderr)                                   # reminder names the related lesson
            subprocess.run(cli + ['--reconcile', 'supersede', ids[0], '--by', ids[1], ids[2]], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(KB.load(db).lessons[ids[0]]['superseded_by'], ids[1:])
            r = subprocess.run(cli + ['related', ids[1]], check=True, stdout=subprocess.PIPE, universal_newlines=True)
            self.assertIn(ids[2], r.stdout)


class TestSources(unittest.TestCase):
    def test_shared_and_project(self):
        kb, w = sample_kb()
        with tempfile.TemporaryDirectory() as d:
            shared, proj = os.path.join(d, 'db'), os.path.join(d, 'db_project')
            os.makedirs(shared); kb.save_shards(shared, backup=False)        # everything starts in the shared KB
            srcs = [('shared', shared), ('project', proj)]
            with transaction(srcs, create=True) as k:
                rec = build_system(GEN, {'formula': 'NH3', 'multiplicity': 1})
                k.add_system(rec)                                             # new system -> project
                sysl = k.add_lesson({'title': 'ammonia', 'text': 'a', 'category': 'method', 'scope': 'system',
                                     'applies_to': {'systems': [rec['id']]}, 'evidence': EV})
                gen = k.add_lesson({'title': 'portable', 'text': 'b', 'category': 'method', 'scope': 'general',
                                    'evidence': EV})
                dec = k.add_lesson({'title': 'decision', 'text': 'c', 'category': 'workflow', 'scope': 'general',
                                    'source': 'user_decision', 'evidence': EV})
            S, P = KB.load(shared), KB.load(proj)
            self.assertIn(rec['id'], P.systems); self.assertIn(sysl, P.lessons); self.assertIn(dec, P.lessons)
            self.assertIn(gen, S.lessons); self.assertNotIn(gen, P.lessons)
            # moving a system alone would leave its one-system lesson dangling in the shared KB: refused
            sl = next(i for i, l in S.lessons.items() if l['title'] == 'system')
            before = read(os.path.join(shared, 'generic.json'))
            with self.assertRaises(KBError):
                with transaction(srcs, mode='reconcile') as k:
                    k.place(w, 'project')
            self.assertEqual(read(os.path.join(shared, 'generic.json')), before)
            with transaction(srcs, mode='reconcile') as k:
                k.place(w, 'project'); k.place(sl, 'project')
            self.assertIn(w, KB.load(proj).systems); self.assertNotIn(w, KB.load(shared).systems)
            m = KB.load_sources(srcs)
            self.assertEqual(m.check_sources()[0], []); m.check(strict=True)
            self.assertEqual(m.role_of('lessons', sl), 'project')
            self.assertEqual(len(m.lessons), len(kb.lessons) + 3)
            KB.load(shared).check()                                           # the shared KB stands alone

    def test_cli_sources(self):
        with tempfile.TemporaryDirectory() as d:
            shared, proj = os.path.join(d, 'db'), os.path.join(d, 'db_project')
            run = lambda *a, ok=True: subprocess.run([sys.executable, os.path.join(ROOT, 'kbcli.py'), '--db', shared] + list(a),
                                                     check=ok, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                     universal_newlines=True)
            run('init'); run('--db', proj, 'init')
            f = os.path.join(d, 'l.json')
            with open(f, 'w') as fh:
                json.dump({'title': 'portable rule', 'text': 't', 'category': 'method', 'scope': 'general',
                           'evidence': EV, 'context': CTX}, fh)
            lid = run('--project', proj, 'add-lesson', '-f', f, '--into', 'project').stdout.strip()
            self.assertIn(lid, KB.load(proj).lessons)
            self.assertNotEqual(run('--project', proj, 'move', lid, '--to', 'shared', ok=False).returncode, 0)
            run('--project', proj, '--reconcile', 'move', lid, '--to', 'shared')
            self.assertIn(lid, KB.load(shared).lessons); self.assertNotIn(lid, KB.load(proj).lessons)
            out = run('--project', proj, 'show', lid).stdout
            self.assertIn('portable rule', out)
            run('--project', proj, 'render')
            self.assertTrue(os.path.exists(os.path.join(d, 'LESSONS.md')))
            self.assertTrue(os.path.exists(os.path.join(proj, 'LESSONS.md')))
            self.assertNotEqual(run('--reconcile', 'move', lid, '--to', 'project', ok=False).returncode, 0)  # no project KB


class TestSupplements(unittest.TestCase):
    def test_sync_and_check(self):
        from lessons_kb import supplements as supp
        with tempfile.TemporaryDirectory() as d:
            proj = os.path.join(d, 'project')
            for sub in ('runA', 'runB'):
                os.makedirs(os.path.join(proj, sub))
            res, big, out = (os.path.join(proj, 'runA', n) for n in ('RESULTS.md', 'big.csv', 'job.out'))
            res_b = os.path.join(proj, 'runB', 'RESULTS.md')
            outside = os.path.join(d, 'elsewhere.py')
            for f, txt in ((os.path.join(proj, 'runA', 'collect.py'), 'print(1)\n'), (outside, 'x\n'),
                           (big, 'x' * 5000), (out, ''.join(f'line {i}\n' for i in range(1, 101))), (res_b, 'B\n')):
                with open(f, 'w') as fh:
                    fh.write(txt)
            with open(res, 'w') as fh:
                fh.write(f'Made by `collect.py`; see also `{outside}` and `big.csv`.\n')
            kb, w = sample_kb()
            fam = kb.add_lesson({'title': 'family', 'text': 't', 'category': 'method', 'scope': 'family',
                                 'applies_to': {'tags': ['open:shell']},
                                 'evidence': [{'path': res, 'note': 'table'}, {'path': big, 'note': 'raw'}]})
            root = os.path.join(d, 'supplements')
            rep = supp.sync(kb, root, [fam], max_size=1000, base=proj, referenced=True)
            ev = kb.lessons[fam]['evidence']
            self.assertEqual(ev[0]['supplement'], 'family/open_shell/RESULTS.md')
            self.assertTrue(os.path.isfile(os.path.join(root, 'family/open_shell/RESULTS.md')))
            self.assertNotIn('supplement', ev[1])                                  # over the size cap
            self.assertEqual([x[1] for x in rep['skipped_large']], [big, big])     # cited, and named in the doc
            man = supp.load_manifest(root)
            self.assertEqual(man['files']['family/open_shell/refs/collect.py']['kind'], 'referenced')
            self.assertFalse(any('elsewhere' in k for k in man['files']))          # outside the base: never copied
            kb.check(strict=True)
            self.assertEqual(supp.check(kb, root), ([], []))
            again = supp.sync(kb, root, [fam], max_size=1000)
            self.assertEqual((again['copied'], again['moved'], again['linked']), ([], [], []))   # idempotent
            # a general lesson citing the same file moves it up a level; a second RESULTS.md gets a prefix
            gen = kb.add_lesson({'title': 'general', 'text': 'g', 'category': 'method', 'scope': 'general',
                                 'evidence': [{'path': res, 'note': 'table'}, {'path': res_b, 'note': 'other'}]})
            rep = supp.sync(kb, root, [gen], max_size=1000)
            self.assertEqual(rep['copied'], ['general/runB__RESULTS.md'])
            self.assertEqual(kb.lessons[fam]['evidence'][0]['supplement'], 'general/runA__RESULTS.md')
            self.assertIn('general/refs/collect.py', supp.load_manifest(root)['files'])
            self.assertFalse(os.path.exists(os.path.join(root, 'family')))         # emptied folders are removed
            self.assertEqual(supp.check(kb, root), ([], []))
            with open(res, 'a') as fh:
                fh.write('new row\n')
            self.assertTrue(any('changed since' in x for x in supp.check(kb, root)[1]))
            supp.sync(kb, root, [fam])
            self.assertEqual(supp.check(kb, root)[1], [])                            # refreshed
            with open(os.path.join(root, 'general/runB__RESULTS.md'), 'a') as fh:
                fh.write('tampered\n')
            self.assertTrue(any('checksum' in x for x in supp.check(kb, root)[1]))
            # an excerpt of a large output, kept on later runs
            l2 = kb.add_lesson({'title': 'output', 'text': 'u', 'category': 'method', 'scope': 'system',
                                'applies_to': {'systems': [w]}, 'evidence': [{'path': out, 'note': 'lines 40-42'}]})
            supp.sync(kb, root, [l2], excerpt=(l2, out, (40, 42)))
            rel = kb.lessons[l2]['evidence'][0]['supplement']
            self.assertTrue(rel.startswith('system/H2O') and rel.endswith('/job.L40-42.out'), rel)
            self.assertEqual(read(os.path.join(root, rel)).splitlines()[1:], ['line 40', 'line 41', 'line 42'])
            supp.sync(kb, root, [])
            self.assertEqual(kb.lessons[l2]['evidence'][0]['supplement'], rel)

    def test_folder_per_kb(self):
        '''shared lessons' copies go beside the shared KB, project lessons' inside the project KB; copies made into
        the shared folder by earlier versions move to the project folder, even when the original is gone'''
        from lessons_kb import supplements as supp
        from lessons_kb.config import load_config
        with tempfile.TemporaryDirectory() as d:
            run = lambda *a: subprocess.run([sys.executable, os.path.join(ROOT, 'kbcli.py')] + list(a), cwd=d,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            with open(os.path.join(d, 'lessons-kb.json'), 'w') as f:
                json.dump({'shared': 'db', 'project': ['db_project']}, f)
            for a in (['init'], ['--db', 'db_project', 'init']):
                self.assertEqual(run(*a).returncode, 0)
            gen_f, sys_f = os.path.join(d, 'general.txt'), os.path.join(d, 'system.log')
            for f in (gen_f, sys_f):
                with open(f, 'w') as fh:
                    fh.write(f + '\n')
            db, dbp = os.path.join(d, 'db'), os.path.join(d, 'db_project')
            shared_root, proj_root = os.path.join(d, 'supplements'), os.path.join(dbp, 'supplements')
            with transaction([('shared', db), ('project', dbp)]) as k:
                k.add_system(build_system(GEN, {'formula': 'H2O', 'multiplicity': 1}, {}))
                w = GEN.system_id({'formula': 'H2O', 'charge': 0, 'multiplicity': 1})
                g = k.add_lesson({'title': 'g', 'text': 'g', 'category': 'method', 'scope': 'general',
                                  'evidence': [{'path': gen_f, 'note': 'n'}], 'context': CTX})
                s = k.add_lesson({'title': 's', 'text': 's', 'category': 'method', 'scope': 'system',
                                  'applies_to': {'systems': [w]}, 'evidence': [{'path': sys_f, 'note': 'n'}],
                                  'context': CTX})
                supp.sync(k, shared_root, [g, s])                 # the old layout: one folder for everything
            os.remove(sys_f)
            r = run('validate'); self.assertIn('cited only by project lessons', r.stdout + r.stderr)
            self.assertEqual(run('supplement', '--shared').returncode, 0)       # keeps the copy a project lesson needs
            self.assertTrue(os.path.isfile(os.path.join(shared_root, 'system/H2O/system.log')))
            r = run('supplement', '--all'); self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(os.path.exists(os.path.join(shared_root, 'system')))
            self.assertEqual(read(os.path.join(proj_root, 'system/H2O/system.log')), sys_f + '\n')
            self.assertEqual(supp.load_manifest(proj_root)['files']['system/H2O/system.log']['source'], sys_f)
            self.assertTrue(os.path.isfile(os.path.join(shared_root, 'general/general.txt')))
            r = run('validate'); self.assertIn('0 errors, 0 warnings', r.stdout, r.stdout + r.stderr)
            r = run('supplement', '--project'); self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn(shared_root + ':', r.stdout)                      # --project leaves the shared folder
            self.assertNotEqual(run('supplement', '--all', s).returncode, 0)   # ids or a set, not both
            # the config may name both folders
            with open(os.path.join(d, 'lessons-kb.json'), 'w') as f:
                json.dump({'shared': 'db', 'supplements': {'shared': 'sup', 'project': 'psup'}}, f)
            cfg = load_config(os.path.join(d, 'lessons-kb.json'))
            self.assertEqual(cfg['supplements'], {'shared': os.path.join(d, 'sup'), 'project': os.path.join(d, 'psup')})
            with open(os.path.join(d, 'lessons-kb.json'), 'w') as f:
                json.dump({'shared': 'db', 'supplements': {'other': 'x'}}, f)
            with self.assertRaises(ValueError):
                load_config(os.path.join(d, 'lessons-kb.json'))


class TestModesAndConflicts(unittest.TestCase):
    def _db(self, d):
        kb, w = sample_kb()
        db = os.path.join(d, 'db'); os.makedirs(db); kb.save_shards(db, backup=False)
        return kb, w, db

    def test_general_mode_is_append_only(self):
        with tempfile.TemporaryDirectory() as d:
            kb, w, db = self._db(d)
            lid = next(iter(kb.lessons))
            for edit in (lambda k: k.supersede(lid, next(i for i in k.lessons if i != lid)),
                         lambda k: k.update_lesson(lid, {'confidence': 'established'}),
                         lambda k: k.rehash(),
                         lambda k: k.update_system(w, {'notes': 'x'}) or k.update_system(w, {'notes': 'y'})):
                with self.assertRaises(KBError):
                    with transaction(db) as k:
                        edit(k)
            with transaction(db) as k:                     # allowed: add, append to lists, fill empty, fill a model once
                k.add_lesson({'title': 'new', 'text': 'n', 'category': 'method', 'scope': 'general', 'evidence': EV,
                              'context': CTX})
                k.update_system(w, {'open_questions': ['q1']}); k.update_system(w, {'open_questions': ['q1', 'q2']})
                k.add_model(w, {'id': 'M2', 'status': 'pending'})
                k.add_model(w, {'id': 'M2', 'status': 'done', 'result_summary': {'mad_cm': 1.0}}, update=True)
            with self.assertRaises(KBError):               # a filled result is not overwritten
                with transaction(db) as k:
                    k.add_model(w, {'id': 'M2', 'result_summary': {'mad_cm': 2.0}}, update=True)
            with self.assertRaises(KBError):               # nor a final status
                with transaction(db) as k:
                    k.add_model(w, {'id': 'M2', 'status': 'failed'}, update=True)
            with self.assertRaises(KBError):               # list items are not removed
                with transaction(db) as k:
                    k.update_system(w, {'open_questions': ['q2']})
            with transaction(db, mode='reconcile') as k:
                k.update_lesson(lid, {'confidence': 'established'})
            self.assertEqual(KB.load(db).lessons[lid]['confidence'], 'established')

    def test_conflicts(self):
        with tempfile.TemporaryDirectory() as d:
            kb, w, db = self._db(d)
            old = next(i for i, l in kb.lessons.items() if l['title'] == 'profile')
            finding = {'title': 'profile, corrected', 'text': 'p2', 'category': 'method', 'scope': 'profile',
                       'applies_to': {'profiles': ['generic']}, 'evidence': EV}
            for bad in (dict(finding), dict(finding, context=CTX)):
                with self.assertRaises(KBError):           # no context; no reason
                    with transaction(db) as k:
                        k.add_conflict(bad, [old], '' if 'context' in bad else 'differs')
            with transaction(db) as k:                     # any session may record a conflict
                cid = k.add_conflict(dict(finding, context=CTX), [old], 'p and p2 disagree')
            self.assertTrue(os.path.isfile(os.path.join(db, 'conflicts.json')))
            back = KB.load(db); back.check(strict=True)    # conflicts.json is not read as a shard
            self.assertEqual(back.contested(old), [cid]); self.assertEqual(len(back.lessons), len(kb.lessons))
            with self.assertRaises(KBError):               # resolving is reconciliation work
                with transaction(db) as k:
                    k.resolve_conflict(cid, 'reject')
            ref = next(i for i in kb.lessons if i != old)
            with transaction(db, mode='reconcile') as k:   # a lesson superseded by the old one gets rewired
                k.supersede(ref, old)
            with transaction(db, mode='reconcile') as k:
                new = k.adopt_finding(cid)
                archived = k.replace(old, new)
                k.resolve_conflict(cid, 'replace', [new], archived, note='more complete')
            back = KB.load(db); back.check(strict=True)
            self.assertNotIn(old, back.lessons); self.assertIn(new, back.lessons)
            self.assertEqual(back.lessons[ref]['superseded_by'], new)
            c = back.conflicts[cid]
            self.assertEqual((c['status'], c['resolution']['archived'][0]['id']), ('resolved', old))
            self.assertEqual(back.contested(new), [])
            # an internal conflict between two existing lessons
            two = sorted(i for i in back.lessons if back.lessons[i]['status'] == 'active')[:2]
            with self.assertRaises(KBError):
                with transaction(db) as k:
                    k.add_conflict(None, two[:1], 'only one', context=CTX)
            with transaction(db) as k:
                c2 = k.add_conflict(None, two, 'they disagree on X', context=CTX)
                with self.assertRaises(KBError):
                    k.mode = 'reconcile'; k.adopt_finding(c2)
                k.mode = 'general'
            KB.load(db).check(strict=True)
            self.assertEqual(KB.load(db).conflicts[c2]['kind'], 'internal')
            # removing a lesson outright (reconciliation), archived in a direct record
            victim = next(i for i, l in KB.load(db).lessons.items() if l['status'] == 'active' and i not in two)
            with self.assertRaises(KBError):
                with transaction(db) as k:
                    k.remove(victim)
            with transaction(db, mode='reconcile') as k:
                k.lessons[victim]['context'] = CTX
                arch = k.remove(victim)
                rid = k.record_direct('remove', [victim], 'user: too inconclusive', by='user', archived=arch)
            back = KB.load(db); back.check(strict=True)
            self.assertNotIn(victim, back.lessons)
            self.assertEqual(back.conflicts[rid]['resolution']['archived'][0]['id'], victim)
            with self.assertRaises(KBError):                # a lesson named by an open conflict can't be removed
                with transaction(db, mode='reconcile') as k:
                    k.remove(two[0])

    def test_routing_and_cli(self):
        with tempfile.TemporaryDirectory() as d:
            shared, proj = os.path.join(d, 'db'), os.path.join(d, 'db_project')
            run = lambda *a, ok=True: subprocess.run([sys.executable, os.path.join(ROOT, 'kbcli.py'), '--db', shared,
                                                      '--project', proj] + list(a), check=ok, stdout=subprocess.PIPE,
                                                     stderr=subprocess.PIPE, universal_newlines=True)
            subprocess.run([sys.executable, os.path.join(ROOT, 'kbcli.py'), '--db', shared, 'init'], check=True,
                           stdout=subprocess.PIPE)
            subprocess.run([sys.executable, os.path.join(ROOT, 'kbcli.py'), '--db', proj, 'init'], check=True,
                           stdout=subprocess.PIPE)
            f = os.path.join(d, 'l.json')
            lesson = {'title': 'rule one', 'text': 'r', 'category': 'method', 'scope': 'general', 'evidence': EV}
            with open(f, 'w') as fh:
                json.dump(lesson, fh)
            self.assertNotEqual(run('add-lesson', '-f', f, ok=False).returncode, 0)     # no context
            with open(f, 'w') as fh:
                json.dump(dict(lesson, context=CTX), fh)
            lid = run('add-lesson', '-f', f).stdout.strip()
            with open(f, 'w') as fh:
                json.dump(dict(lesson, title='rule one is wrong', text='w', context=CTX), fh)
            out = run('conflict', 'add', '-f', f, '--contradicts', lid, '--why', 'w vs r').stdout
            self.assertIn('stored in the shared KB', out)
            cid = out.split()[0]
            self.assertTrue(os.path.isfile(os.path.join(shared, 'conflicts.json')))
            self.assertIn(f'[contested: {cid}]', run('lessons').stdout)
            with open(f, 'w') as fh:                        # a failed write prints no success line
                json.dump(dict(lesson, title='dangling', context=CTX, references=['L-0000000000']), fh)
            r = run('add-lesson', '-f', f, ok=False)
            self.assertEqual((r.returncode, r.stdout), (2, '')); self.assertIn('nothing was written', r.stderr)
            self.assertIn(cid, run('conflicts', '--open').stdout)
            self.assertIn('rule one is wrong', run('conflict', 'show', cid).stdout)
            self.assertNotEqual(run('conflict', 'resolve', cid, '--action', 'reject', ok=False).returncode, 0)
            run('--reconcile', 'conflict', 'resolve', cid, '--action', 'reject', '--note', 'evidence was wrong')
            self.assertEqual(run('conflicts', '--open').stdout.strip(), 'no conflicts')
            self.assertIn('0 open conflicts', run('validate', '--strict').stdout)


class TestLinks(unittest.TestCase):
    def test_references_and_mapped_replace(self):
        with tempfile.TemporaryDirectory() as d:
            kb, w = sample_kb()
            db = os.path.join(d, 'db'); os.makedirs(db); kb.save_shards(db, backup=False)
            mk = lambda t, txt, **kw: dict({'title': t, 'text': txt, 'category': 'method', 'scope': 'general',
                                            'evidence': EV, 'context': CTX}, **kw)
            with transaction(db) as k:                     # a new lesson may point at old ones (general mode)
                a = k.add_lesson(mk('alpha basis rule', 'alpha basis convergence for crystal fields'))
                b = k.add_lesson(mk('beta grid rule', 'beta grid convergence for crystal fields'))
                c = k.add_lesson(mk('gamma builds on alpha', 'g', references=[{'id': a, 'rel': 'builds_on'}, b]))
                with self.assertRaises(KBError):
                    k.add_lesson(mk('bad', 'x', references=[{'id': a, 'rel': 'likes'}]))
                with self.assertRaises(KBError):            # a reference must name an existing lesson
                    k.add_lesson(mk('bad', 'x', references=['L-0000000000']))
            back = KB.load(db); back.check(strict=True)
            self.assertEqual(back.lessons[a]['referenced_by'], [{'id': c, 'rel': 'builds_on'}])
            self.assertEqual(back.lessons[b]['referenced_by'], [{'id': c, 'rel': 'see_also'}])
            stored = json.loads(read(os.path.join(db, 'general.json')))['lessons'][a]
            self.assertEqual(stored['referenced_by'], [{'id': c, 'rel': 'builds_on'}])    # stored in the json too
            with self.assertRaises(KBError):               # derived: can't be set
                with transaction(db, mode='reconcile') as k:
                    k.update_lesson(a, {'referenced_by': []})
            with transaction(db, mode='reconcile') as k:   # history: h1 superseded by a, h2 by b
                h1 = k.add_lesson(mk('alpha old', 'alpha old')); h2 = k.add_lesson(mk('beta old', 'beta old'))
                k.supersede(h1, a); k.supersede(h2, b)
            with transaction(db, mode='reconcile') as k:
                a2 = k.add_lesson(mk('alpha basis rule, restated', 'alpha basis convergence for crystal fields, all data'))
                b2 = k.add_lesson(mk('beta grid rule, restated', 'beta grid convergence for crystal fields, all data'))
                with self.assertRaises(KBError):           # several by several needs a mapping
                    k.replace([a, b], [a2, b2])
                arch = k.replace([a, b], [a2, b2], mapping='auto')
                self.assertEqual(sorted(x['id'] for x in arch), sorted([a, b]))
            back = KB.load(db); back.check(strict=True)
            self.assertEqual(back.lessons[h1]['superseded_by'], a2)          # each history lesson to its own successor
            self.assertEqual(back.lessons[h2]['superseded_by'], b2)
            self.assertEqual(back.lessons[c]['references'], [{'id': a2, 'rel': 'builds_on'}, {'id': b2, 'rel': 'see_also'}])
            self.assertIn({'id': c, 'rel': 'builds_on'}, back.lessons[a2]['referenced_by'])
            self.assertIn({'id': h1, 'rel': 'superseded'}, back.lessons[a2]['referenced_by'])


class TestConfig(unittest.TestCase):
    def test_config_found_upward(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ); env.pop('LESSONS_KB', None); env.pop('LESSONS_KB_PROJECT', None)
            env.pop('LESSONS_KB_CONFIG', None)
            run = lambda *a, cwd=d, ok=True: subprocess.run([sys.executable, os.path.join(ROOT, 'kbcli.py')] + list(a),
                                                            cwd=cwd, env=env, check=ok, stdout=subprocess.PIPE,
                                                            stderr=subprocess.PIPE, universal_newlines=True)
            self.assertNotEqual(run('lessons', ok=False).returncode, 0)          # no KB here yet
            run('init')                                                           # writes lessons-kb.json + db/
            self.assertTrue(os.path.isfile(os.path.join(d, 'lessons-kb.json')))
            run('--db', os.path.join(d, 'db_project'), 'init')
            prof = os.path.join(d, 'myprofiles', 'toy'); os.makedirs(prof)
            with open(os.path.join(prof, 'profile.py'), 'w') as f:
                f.write('from lessons_kb.profile import Field, Profile\n'
                        'class Toy(Profile):\n    name = "toy"\n    version = 1\n    description = "toy"\n'
                        '    fields = [Field("label", required=True)]\n'
                        '    def fingerprint(self, ident, inputs):\n        return {}\n'
                        'PROFILE = Toy()\n')
            with open(os.path.join(d, 'lessons-kb.json'), 'w') as f:
                json.dump({'shared': 'db', 'project': ['db_project'], 'profiles': ['myprofiles']}, f)
            sub = os.path.join(d, 'work', 'deep'); os.makedirs(sub)
            self.assertIn('toy', run('profiles', cwd=sub).stdout)                # profile dirs from the config
            lf = os.path.join(d, 'l.json')
            with open(lf, 'w') as f:
                json.dump({'title': 'cfg rule', 'text': 't', 'category': 'method', 'scope': 'system',
                           'evidence': EV, 'context': CTX, 'applies_to': {'systems': []}}, f)
            with open(lf, 'w') as f:
                json.dump({'title': 'cfg decision', 'text': 't', 'category': 'workflow', 'scope': 'general',
                           'source': 'user_decision', 'evidence': EV, 'context': CTX}, f)
            lid = run('add-lesson', '-f', lf, cwd=sub).stdout.strip()             # found upward from work/deep
            self.assertIn(lid, KB.load(os.path.join(d, 'db_project')).lessons)   # routed to the project KB
            self.assertIn(lid, run('lessons', cwd=sub).stdout)
            self.assertNotIn(lid, run('--db', os.path.join(d, 'db'), 'lessons', cwd=sub).stdout)  # --db overrides


class TestRehash(unittest.TestCase):
    def test_version_bump(self):
        kb, w = sample_kb()
        Gen = type(GEN)

        class V2(Gen):
            version = '2'

            def canonical(self, ident):
                return super().canonical(ident) + ';v2'
        v2 = V2(); v2._dir = GEN._dir
        kb2 = KB(copy.deepcopy(kb.data), profiles={'generic': v2})
        self.assertTrue(any('rehash' in x for x in kb2.validate()[1]))
        mapping, conf = kb2.rehash()
        self.assertEqual(len(mapping), len(kb.systems)); self.assertEqual(conf, [])
        kb2.check(strict=True)
        sys_lesson = next(l for l in kb2.lessons.values() if l['title'] == 'system')
        self.assertEqual(sys_lesson['applies_to']['systems'], [mapping[w]])


class TestSetup(unittest.TestCase):
    def test_setup_condition(self):
        kb, w = sample_kb()
        lid = kb.add_lesson({'title': 'quirk', 'text': 'q', 'category': 'software', 'scope': 'general',
                             'applies_to': {'software': ['PySCF'], 'platform': ['Perlmutter']}, 'evidence': EV})
        l = kb.lessons[lid]
        self.assertEqual(l['applies_to']['software'], ['pyscf'])  # normalized
        water = kb.systems[w]
        ids = lambda **kw: {x['id'] for x in kb.lessons_for(system=water, **kw)}
        self.assertIn(lid, ids())                                                     # undeclared: kept
        self.assertIn(lid, ids(setup={'software': ['pyscf:2.6']}))                    # detail matches; platform undeclared
        self.assertIn(lid, ids(setup={'software': ['pyscf'], 'platform': ['perlmutter']}))
        self.assertNotIn(lid, ids(setup={'software': ['orca']}))                      # conflict: dropped
        self.assertNotIn(lid, ids(setup={'software': ['pyscf'], 'platform': ['laptop']}))
        plain = next(x['id'] for x in kb.lessons.values() if x['title'] == 'general')
        self.assertIn(plain, ids(setup={'software': ['orca']}))                       # unconditioned lessons always apply
        E, W = kb.validate()
        self.assertEqual(E, []); self.assertFalse(any(lid in x for x in W))
        kb.add_lesson({'title': 'bare quirk', 'text': 'b', 'category': 'software', 'scope': 'general', 'evidence': EV})
        self.assertTrue(any('without applies_to.software' in x for x in kb.validate()[1]))
        with self.assertRaises(KBError):
            kb.add_lesson({'title': 'x', 'text': 'x', 'category': 'software', 'scope': 'general',
                           'applies_to': {'machine': ['a']}, 'evidence': EV})
        kb.lessons[lid]['applies_to']['software'] = ['Py SCF']                       # hand edit
        self.assertTrue(any('lower-case' in e for e in kb.validate()[0]))


class TestShardDir(unittest.TestCase):
    def test_routed_writes(self):
        kb, w = sample_kb()
        kb.add_lesson({'title': 'site quirk', 'text': 'q', 'category': 'software', 'scope': 'general',
                       'applies_to': {'software': ['x'], 'platform': ['siteA:fs']}, 'evidence': EV})
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, 'db'); os.makedirs(db)
            with transaction(db, create=True) as t:
                for s in kb.systems.values():
                    t.add_system(copy.deepcopy(s))
                for l in kb.lessons.values():
                    t.data['lessons'][l['id']] = copy.deepcopy(l)
            names = sorted(os.listdir(db))
            self.assertEqual([n for n in names if n.endswith('.json')], ['general.json', 'generic.json', 'platform_sitea.json'])
            g = json.loads(read(os.path.join(db, 'general.json')))
            # general scope, and tag families not tied to one profile, go to general.json; profile/system lessons to generic.json
            self.assertEqual(sorted(l['title'] for l in g['lessons'].values()), ['family', 'general', 'orphan'])
            back = KB.load(db)
            self.assertEqual(set(back.lessons), set(kb.lessons)); self.assertEqual(set(back.systems), set(kb.systems))
            back.check(strict=True)
            # moving a lesson between shards (adding a platform) and emptying a shard
            lid = next(l['id'] for l in kb.lessons.values() if l['title'] == 'site quirk')
            with transaction(db, mode='reconcile') as t:
                t.lessons[lid]['applies_to']['platform'] = []
                t.update_lesson(lid, {'applies_to': {'software': ['x']}})
            self.assertFalse(os.path.exists(os.path.join(db, 'platform_sitea.json')))
            self.assertIn(lid, json.loads(read(os.path.join(db, 'general.json')))['lessons'])
            before = os.path.getmtime(os.path.join(db, 'generic.json'))
            with transaction(db) as t:
                t.add_lesson({'title': 'g2', 'text': 'g2', 'category': 'workflow', 'scope': 'general', 'evidence': EV})
            self.assertEqual(os.path.getmtime(os.path.join(db, 'generic.json')), before)   # untouched shard
            with self.assertRaises(KBError):                                               # failed validation: no change
                with transaction(db) as t:
                    t.data['lessons']['L-bad'] = {'id': 'L-bad'}
            self.assertEqual(len(KB.load(db).lessons), len(kb.lessons) + 1)

    def test_cli_default_is_dir(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, 'db')
            r = subprocess.run([sys.executable, os.path.join(ROOT, 'kbcli.py'), '--db', db, 'init'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.path.exists(os.path.join(db, 'general.json')))


class TestRender(unittest.TestCase):
    def test_render(self):
        kb, _ = sample_kb()
        md = kb.render()
        self.assertIn('H2O', md); self.assertIn('Scope: general', md)


class TestMetadata(unittest.TestCase):
    def _db(self, d):
        kb, w = sample_kb()
        db = os.path.join(d, 'db'); os.makedirs(db); kb.save_shards(db, backup=False)
        return kb, db, next(i for i, l in kb.lessons.items() if l['title'] == 'general')

    def test_metadata_editable_in_general_mode(self):
        with tempfile.TemporaryDirectory() as d:
            kb, db, lid = self._db(d)
            with transaction(db) as k:
                k.update_lesson(lid, {'evidence': [{'path': '/dev/null', 'kind': 'output', 'lines': '3-5',
                                                    'quote': 'E = -1.0', 'supports': 'the energy'}],
                                      'coverage': ['water'], 'reviewed': True})
                k.update_lesson(lid, {'evidence': [{'path': '/dev/zero', 'note': 'second file'}]})
                k.add_verification(lid, {'verdict': 'partly', 'by': 'tester', 'note': 'one claim'})
            l = KB.load(db).lessons[lid]
            self.assertEqual(len(l['evidence']), 2)                     # annotated in place, then one appended
            self.assertEqual(l['evidence'][0], {'path': '/dev/null', 'note': 'test', 'kind': 'output', 'lines': '3-5',
                                                'quote': 'E = -1.0', 'supports': 'the energy'})
            self.assertEqual((l['coverage'], l['reviewed']), (['water'], True))
            self.assertEqual(l['verification'][-1]['verdict'], 'partly')
            self.assertEqual(l['id'], lid)                              # metadata is not part of the id
            with transaction(db) as k:                                  # None removes a key
                k.update_lesson(lid, {'evidence': [{'path': '/dev/null', 'quote': None}]})
            self.assertNotIn('quote', KB.load(db).lessons[lid]['evidence'][0])
            for bad in ({'confidence': 'established'}, {'tags': ['x']}, {'context': CTX},
                        {'evidence': [{'path': '/dev/zero', 'supplement': 'general/zero'}]},   # written by supplement
                        {'evidence': [{'path': '/new', 'kind': 'output'}]},                    # a new entry needs a note
                        {'evidence': [{'path': '/dev/null', 'kind': 'guess'}]},
                        {'evidence': [{'path': '/dev/null', 'lines': '9-2'}]},
                        {'verification': [{'verdict': 'fine'}]}):
                with self.assertRaises(KBError):
                    with transaction(db) as k:
                        k.update_lesson(lid, bad)
            with self.assertRaises(KBError):                            # narrowing applies_to is reconciliation
                with transaction(db) as k:
                    k.update_lesson(lid, {'coverage': ['water']}, replace=True)

    def test_evidence_entries_sharing_a_path(self):
        with tempfile.TemporaryDirectory() as d:
            kb, db, lid = self._db(d)
            with transaction(db) as k:
                k.update_lesson(lid, {'evidence': [{'path': '/dev/null', 'note': 'other lines', 'lines': '10-12'}]})
            self.assertEqual(len(KB.load(db).lessons[lid]['evidence']), 1)    # same path: annotated, not appended
            with transaction(db, mode='reconcile') as k:
                k.lessons[lid]['evidence'].append({'path': '/dev/null', 'note': 'second', 'lines': '20'})
            with self.assertRaises(KBError):                            # ambiguous without index or matching lines
                with transaction(db) as k:
                    k.update_lesson(lid, {'evidence': [{'path': '/dev/null', 'quote': 'q'}]})
            with transaction(db) as k:
                k.update_lesson(lid, {'evidence': [{'path': '/dev/null', 'index': 1, 'quote': 'q1'}]})
                k.update_lesson(lid, {'evidence': [{'path': '/dev/null', 'lines': '10-12', 'quote': 'q0'}]})
            ev = KB.load(db).lessons[lid]['evidence']
            self.assertEqual([e.get('quote') for e in ev], ['q0', 'q1'])

    def test_universal_words(self):
        kb, _ = sample_kb()
        base = {'category': 'method', 'scope': 'general', 'evidence': EV, 'context': CTX}
        ids = {t: kb.add_lesson(dict(base, title=t, text=x)) for t, x in (
            ('u1', 'This holds for every spin state.'), ('u2', 'All-electron and never-converging runs differ.'),
            ('u3', 'Always restart from converged orbitals.'), ('u4', 'You must check the orbitals.'))}
        _, W = kb.validate()
        flagged = {i for i in ids.values() if any(i in w and 'without coverage' in w for w in W)}
        self.assertEqual(flagged, {ids['u1'], ids['u3'], ids['u4']})    # hyphenated compounds are not flagged
        kb.mode = 'general'
        kb.update_lesson(ids['u1'], {'coverage': ['singlet', 'triplet']})
        _, W = kb.validate()
        self.assertFalse(any(ids['u1'] in w and 'without coverage' in w for w in W))

    def test_cli_evidence_and_verify(self):
        with tempfile.TemporaryDirectory() as d:
            kb, db, lid = self._db(d)
            run = lambda *a: subprocess.run([sys.executable, os.path.join(ROOT, 'kbcli.py'), '--db', db] + list(a),
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            r = run('evidence', lid, '--path', '/dev/null', '--kind', 'output', '--lines', '7', '--quote', 'x')
            self.assertEqual(r.returncode, 0, r.stderr)
            r = run('verify', lid, '--verdict', 'supported', '--by', 'tester')
            self.assertEqual(r.returncode, 0, r.stderr)
            l = KB.load(db).lessons[lid]
            self.assertEqual((l['evidence'][0]['lines'], l['verification'][0]['verdict']), ('7', 'supported'))
            md = KB.load(db).render()
            self.assertIn('Verification: supported', md); self.assertIn('/dev/null`:7', md)

    def test_supplement_base_of_one_cited_file(self):
        from lessons_kb import supplements as supp
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, 'run', 'out.txt'); os.makedirs(os.path.dirname(src))
            with open(src, 'w') as f:
                f.write('E = -1\n')
            kb = KB()
            lid = kb.add_lesson({'title': 't', 'text': 'x', 'category': 'method', 'scope': 'general',
                                 'evidence': [{'path': src, 'note': 'n'}]})
            supp.sync(kb, os.path.join(d, 'supplements'), [lid])
            self.assertEqual(supp.load_manifest(os.path.join(d, 'supplements'))['base'], os.path.dirname(src))


if __name__ == '__main__':
    unittest.main(verbosity=1)
