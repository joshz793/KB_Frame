'''Shared, profile-independent helpers: formula parsing, xyz reading, and invariant structural descriptors
with a kernel similarity. Everything here depends only on numpy and the standard library.'''
import re
from collections import Counter, OrderedDict
from functools import reduce
from math import gcd

import numpy as np

# ---------------------------------------------------------------- formulas
_TOKEN = re.compile(r'([A-Z][a-z]?|\(|\)|\d+)')


def parse_formula(formula):
    '''"Ca(OH)2" -> Counter({'O': 2, 'H': 2, 'Ca': 1}). Integer counts and nested parentheses only;
    raises ValueError on anything else (charges, decimals, hydrates written with dots).'''
    s = formula.replace(' ', '')
    toks = _TOKEN.findall(s)
    if ''.join(toks) != s or not toks:
        raise ValueError(f'cannot parse formula {formula!r}')
    stack = [Counter()]
    i = 0
    while i < len(toks):
        t = toks[i]
        if t == '(':
            stack.append(Counter()); i += 1; continue
        if t == ')':
            grp = stack.pop(); i += 1; n = 1
            if i < len(toks) and toks[i].isdigit():
                n = int(toks[i]); i += 1
            if not stack:
                raise ValueError(f'unbalanced ")" in {formula!r}')
            for k, v in grp.items():
                stack[-1][k] += v * n
            continue
        if t.isdigit():
            raise ValueError(f'misplaced number in {formula!r}')
        i += 1; n = 1
        if i < len(toks) and toks[i].isdigit():
            n = int(toks[i]); i += 1
        stack[-1][t] += n
    if len(stack) != 1:
        raise ValueError(f'unbalanced "(" in {formula!r}')
    return stack[0]


def reduce_counts(counts):
    '''divide all counts by their gcd (formula units of a crystal: Y2O3 stays Y2O3, Ca2W2O8 -> CaWO4)'''
    g = reduce(gcd, counts.values())
    return Counter({k: v // g for k, v in counts.items()})


def hill_formula(counts):
    '''Hill order: C first, then H, then alphabetical; without C, everything alphabetical'''
    keys = sorted(counts)
    if 'C' in counts:
        keys = ['C'] + (['H'] if 'H' in counts else []) + [k for k in keys if k not in ('C', 'H')]
    return ''.join(f'{k}{counts[k] if counts[k] != 1 else ""}' for k in keys)


def composition_similarity(a, b):
    '''weighted Jaccard on mole fractions of two composition dicts, in [0, 1]'''
    ta, tb = sum(a.values()), sum(b.values())
    if not ta or not tb:
        return None
    keys = set(a) | set(b)
    fa = {k: a.get(k, 0) / ta for k in keys}; fb = {k: b.get(k, 0) / tb for k in keys}
    return sum(min(fa[k], fb[k]) for k in keys) / sum(max(fa[k], fb[k]) for k in keys)


# ---------------------------------------------------------------- xyz
def element_of(label):
    '''atom label -> element symbol ("Y_gh" -> "Y", "O1" -> "O", "Er3+" -> "Er"); "X..." labels stay "X"'''
    m = re.match(r'([A-Z][a-z]?)', label)
    if not m:
        raise ValueError(f'no element symbol in atom label {label!r}')
    return m.group(1)


def read_xyz(path):
    '''Read an xyz file: line 1 = atom count, line 2 = comment (returned verbatim, so profiles can interpret
    custom headers), then "label x y z [extra columns...]". Returns a dict with labels, elements,
    coords (n x 3 array, file units), extra (list of the extra columns as strings) and comment.'''
    with open(path) as f:
        lines = [l.rstrip('\n') for l in f]
    n = int(lines[0].split()[0])
    rows = [l.split() for l in lines[2:2 + n]]
    if len(rows) != n or any(len(r) < 4 for r in rows):
        raise ValueError(f'{path}: expected {n} atom lines with at least 4 columns')
    labels = [r[0] for r in rows]
    return {'labels': labels, 'elements': [element_of(l) for l in labels],
            'coords': np.array([[float(x) for x in r[1:4]] for r in rows]),
            'extra': [r[4:] for r in rows], 'comment': lines[1]}


# ---------------------------------------------------------------- invariant descriptors
def pair_distances(elements, coords, rmax=10.0, ndigits=3):
    '''element-pair-resolved interatomic distances up to rmax: {"C-H": [1.09, ...], ...}, sorted. Invariant to
    rotation, translation and atom order. Suitable for molecules and small clusters (O(n^2) storage).'''
    out = {}
    X = np.asarray(coords, float)
    for i in range(len(elements)):
        d = np.linalg.norm(X[i + 1:] - X[i], axis=1)
        for j in np.nonzero(d <= rmax)[0]:
            key = '-'.join(sorted((elements[i], elements[i + 1 + j])))
            out.setdefault(key, []).append(round(float(d[j]), ndigits))
    return OrderedDict((k, sorted(v)) for k, v in sorted(out.items()))


def radial_shells(elements, coords, center, rmax=7.0, tol=0.15, ndigits=3):
    '''shells around one centre: [[element, count, mean_r], ...] sorted by r, grouping same-element
    neighbours whose distances differ by < tol (single linkage). Invariant to rotation and atom order.'''
    X = np.asarray(coords, float); c = np.asarray(center, float)
    r = np.linalg.norm(X - c, axis=1)
    keep = [(elements[i], float(r[i])) for i in np.argsort(r) if 1e-6 < r[i] <= rmax]
    shells = []
    for el in sorted({e for e, _ in keep}):
        ds = sorted(d for e, d in keep if e == el)
        grp = [ds[0]]
        for d in ds[1:]:
            if d - grp[-1] < tol:
                grp.append(d)
            else:
                shells.append([el, len(grp), round(sum(grp) / len(grp), ndigits)]); grp = [d]
        shells.append([el, len(grp), round(sum(grp) / len(grp), ndigits)])
    return sorted(shells, key=lambda s: (s[2], s[0]))


def _k(a, b, sigma):
    if not a or not b:
        return 0.0
    ra = np.array([x[0] for x in a]); wa = np.array([x[1] for x in a], float)
    rb = np.array([x[0] for x in b]); wb = np.array([x[1] for x in b], float)
    return float(wa @ np.exp(-(ra[:, None] - rb[None, :]) ** 2 / (4 * sigma ** 2)) @ wb)


def kernel_similarity(A, B, sigma=0.1):
    '''cosine similarity of Gaussian-smeared distance densities. A, B: {key: [(r, weight), ...]}; only equal
    keys interact. Returns a value in [0, 1], or None if either side is empty.'''
    num = sum(_k(A[k], B[k], sigma) for k in A if k in B)
    na = sum(_k(v, v, sigma) for v in A.values()); nb = sum(_k(v, v, sigma) for v in B.values())
    if na <= 0 or nb <= 0:
        return None
    return max(0.0, min(1.0, num / np.sqrt(na * nb)))


def as_weighted(d):
    '''{key: [r, ...]} -> {key: [(r, 1), ...]} for kernel_similarity'''
    return {k: [(r, 1.0) for r in v] for k, v in d.items()}


def shells_as_weighted(shells, key=lambda el: el):
    '''[[el, count, r], ...] -> {key(el): [(r, count), ...]}; key lets a profile group elements into classes'''
    out = {}
    for el, n, r in shells:
        out.setdefault(key(el), []).append((r, n))
    return out
