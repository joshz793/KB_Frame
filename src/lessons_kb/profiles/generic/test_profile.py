'''generic profile tests (stdlib unittest). Run: python profiles/generic/test_profile.py'''
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(HERE))))   # src/, so lessons_kb imports
from lessons_kb.core import build_system, check_calibration  # noqa: E402
from lessons_kb.profile import load_profiles  # noqa: E402

P = load_profiles()['generic']
EX = os.path.join(HERE, 'examples')


class TestGeneric(unittest.TestCase):
    def test_fingerprint_invariance(self):
        for m in ('water', 'methane', 'ethanol', 'dimethyl_ether'):
            a = P.fingerprint({}, {'xyz': os.path.join(EX, f'{m}.xyz')})['pair_distances']
            b = P.fingerprint({}, {'xyz': os.path.join(EX, f'{m}_rot.xyz')})['pair_distances']
            self.assertEqual(a.keys(), b.keys(), m)
            for k in a:
                self.assertEqual(len(a[k]), len(b[k]))
                self.assertTrue(all(abs(x - y) <= 1.01e-3 for x, y in zip(a[k], b[k])), (m, k))

    def test_identity(self):
        base = {'formula': 'C2H6O', 'multiplicity': 1}
        ids = {P.system_id(P.normalize_identity(dict(base, **x))) for x in
               ({}, {'charge': 1, 'multiplicity': 2}, {'multiplicity': 3}, {'descriptors': {'isomer': 'ethanol'}},
                {'state': 'S1'})}
        self.assertEqual(len(ids), 5)
        self.assertEqual(P.system_id(P.normalize_identity({'formula': 'CH3CH2OH', 'multiplicity': 1})),
                         P.system_id(P.normalize_identity(base)))
        with self.assertRaises(ValueError):
            P.normalize_identity({'formula': 'H2O'})             # multiplicity is required
        with self.assertRaises(ValueError):
            P.normalize_identity({'formula': 'H2O', 'multiplicity': 1, 'colour': 'blue'})

    def test_xyz_formula_check(self):
        s = build_system(P, {'formula': 'H2O', 'multiplicity': 1}, {'xyz': os.path.join(EX, 'methane.xyz')})
        self.assertFalse(s['fingerprint']['xyz_matches_formula'])

    def test_calibration(self):
        res = check_calibration(P)
        self.assertTrue(res)
        for d, ok, det in res:
            self.assertTrue(ok, f'{d}: {det}')


if __name__ == '__main__':
    unittest.main(verbosity=1)
