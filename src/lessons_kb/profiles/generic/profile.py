'''generic profile: any molecule or cluster identified by formula, charge, spin multiplicity and optional
labels. Use it when no specialized profile fits; see README.md here and ../../GUIDE.md.'''
from lessons_kb.geometry import (as_weighted, composition_similarity, hill_formula, kernel_similarity, pair_distances,
                         parse_formula, read_xyz)
from lessons_kb.profile import Field, Profile


def _formula(v):
    return hill_formula(parse_formula(v))


def _descriptors(d):
    return {str(k).strip().lower(): str(v).strip() for k, v in sorted(d.items()) if str(v).strip()}


class Generic(Profile):
    name = 'generic'
    version = '1'
    description = 'any molecule/cluster: Hill formula + charge + multiplicity (+ optional state label and descriptors)'
    fields = [
        Field('formula', 'str', normalize=_formula, doc='chemical formula, any element order (stored in Hill order, not reduced)'),
        Field('charge', 'int', default=0, doc='total charge'),
        Field('multiplicity', 'int', doc='spin multiplicity 2S+1'),
        Field('state', 'str', required=False, normalize=str.strip,
              doc='optional label that makes the entry distinct (electronic state, conformer, ...)'),
        Field('descriptors', 'dict', required=False, normalize=_descriptors,
              doc='optional {key: value} that distinguishes otherwise identical formulas (e.g. isomer name or a line notation)'),
        Field('name', 'str', required=False, in_key=False, doc='display name only; does not change the id'),
    ]
    fingerprint_inputs = {'xyz': 'plain xyz geometry (count line, comment line, "El x y z" lines; Angstrom)'}
    components = {'composition': 0.4, 'tags': 0.2, 'geometry': 0.4}
    tag_vocabulary = {'charged': 'net charge != 0 (added automatically)',
                      'open_shell': 'multiplicity > 1 (added automatically)'}
    strict_tags = False

    def alias(self, ident):
        base = ident.get('name') or ident['formula']
        q = ident.get('charge', 0)
        base += '' if not q else f"({q:+d})"
        if ident.get('state'):
            base += f" [{ident['state']}]"
        if ident.get('descriptors') and not ident.get('name'):
            base += ' {' + ','.join(f'{k}={v}' for k, v in ident['descriptors'].items()) + '}'
        return base

    def fingerprint(self, ident, inputs):
        fp = {}
        if 'formula' in ident:
            fp['composition'] = dict(sorted(parse_formula(ident['formula']).items()))
        if inputs.get('xyz'):
            g = read_xyz(inputs['xyz'])
            fp['natoms'] = len(g['elements'])
            fp['pair_distances'] = pair_distances(g['elements'], g['coords'])
            comp = {}
            for e in g['elements']:
                comp[e] = comp.get(e, 0) + 1
            if 'composition' in fp:
                fp['xyz_matches_formula'] = comp == fp['composition']
            else:
                fp['composition'] = dict(sorted(comp.items()))
        return fp

    def suggest_tags(self, ident, fp):
        tags = []
        if ident.get('charge'):
            tags.append('charged')
        if ident.get('multiplicity', 1) > 1:
            tags.append('open_shell')
        return tags

    def sim_composition(self, a, b):
        ca, cb = a['fingerprint'].get('composition'), b['fingerprint'].get('composition')
        return composition_similarity(ca, cb) if ca and cb else None

    def sim_geometry(self, a, b):
        pa, pb = a['fingerprint'].get('pair_distances'), b['fingerprint'].get('pair_distances')
        if not pa or not pb:
            return None
        return kernel_similarity(as_weighted(pa), as_weighted(pb), sigma=0.1)


PROFILE = Generic()
