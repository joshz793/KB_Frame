# Profile `generic` (version 1)

## Scope
Any molecule or finite cluster, when no specialized profile fits. Its similarity score uses composition and the overall geometry, so it can't distinguish fine distinctions that matter for a particular class of question. When a class of systems keeps needing distinctions this profile can't make, write a specialized profile (GUIDE.md §8).

## Identity

| Field | Kind | Required | In key | Normalization | Why it is part of identity |
|---|---|---|---|---|---|
| `formula` | str | yes | yes | Parsed (nested parentheses OK), written in Hill order, **not** reduced (C2H4 ≠ CH2) | What the system is made of |
| `charge` | int | yes (default 0) | yes | — | A different charge state is a different system |
| `multiplicity` | int | yes | yes | — | A different spin state is a different system |
| `state` | str | no | yes | Stripped | Keeps apart entries with the same formula, charge and spin that must not be pooled (e.g. an excited state, a conformer) |
| `descriptors` | dict | no | yes | Keys lower-cased; sorted; empty values dropped | Distinguishes isomers or variants, e.g. `{"isomer": "ethanol"}` |
| `name` | str | no | **no** | — | Display only |

Without `descriptors`, ethanol and dimethyl ether (both C2H6O, singlet) would share one id. Add whatever descriptor separates entries whose results must not be pooled.

## Fingerprint
- **Input:** `xyz`, a plain xyz file in Å, optional.
- **Stored:**
  - `composition`, from the formula;
  - when an xyz is given: `natoms`, `pair_distances` (element-pair-resolved sorted distances ≤ 10 Å, 3 decimals) and `xyz_matches_formula`.
- **Invariance:** rotation, translation and atom order.
- **Limits:** storage grows as O(n²), so this is suited to molecules and small clusters, not large ones.

## Similarity

| Component | Weight | Definition |
|---|---|---|
| `composition` | 0.4 | Weighted Jaccard on mole fractions |
| `tags` | 0.2 | Tag Jaccard (base-class `sim_tags`) |
| `geometry` | 0.4 | Cosine overlap of Gaussian-smeared (σ = 0.1 Å) pair-distance densities, per element pair |

- A component that one record cannot supply (e.g. no xyz) counts as the neutral 0.5. A component the query cannot supply at all (e.g. an untagged query) is dropped (GUIDE §8).
- Identical geometry and composition score 1.
- Isomers score about 0.8. Unrelated molecules score below 0.4.
- The weights are a neutral starting point and have not been tuned for any class.

## Calibration
`kbcli calibrate --profile generic`. All 5 checks pass:

| Check | Score |
|---|---|
| water ~ rotated/permuted water | 1.0 |
| ethanol ~ rotated/permuted ethanol | 1.0 |
| water !~ methane | 0.28 |
| ethanol: ethanol_rot > dimethyl ether | 1.0 > 0.83 |
| ethanol: dimethyl ether > water | 0.83 > 0.38 |

## Tags
- Added automatically: `charged` (charge ≠ 0) and `open_shell` (multiplicity > 1).
- Free tags are allowed (`strict_tags = False`).

## Lesson categories
Core only.

## Result fields
None declared: `result_summary` is free-form.

## Data sources
`examples/` holds the calibration geometries.

## Seeding notes
No production systems are seeded with this profile.

## Known limitations
- No symmetry or bonding-topology descriptors.
- Geometry is compared globally, so a small change at an important site scores like a small change anywhere.
- Specialized profiles should focus their descriptors on what matters for their class.
