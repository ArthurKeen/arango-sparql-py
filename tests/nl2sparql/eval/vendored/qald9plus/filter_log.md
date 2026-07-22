# QALD-9-plus D-06 filter log

**Total input questions (train+test combined):** 558
- train: 408
- test: 150

**Kept:** 514
- train: 394
- test: 120

**Dropped:** 44

| Reason | Count |
|--------|-------|
| non-transpilable | 20 |
| parse-fail | 24 |

**Reconciliation:** kept (514) + dropped (44) = 558 == total input (558).

**Multiple-English-paraphrase questions:** 0 (Task 1's raw pruning already reduced every question to a single `en` entry).

## Per-reason detail

### non-transpilable (20)

- qald9plus-train-41
- qald9plus-train-54
- qald9plus-train-68
- qald9plus-train-78
- qald9plus-train-100
- qald9plus-train-102
- qald9plus-train-114
- qald9plus-train-116
- qald9plus-train-128
- qald9plus-train-142
- qald9plus-train-186
- qald9plus-train-235
- qald9plus-test-4
- qald9plus-test-29
- qald9plus-test-23
- qald9plus-test-199
- qald9plus-test-122
- qald9plus-test-7
- qald9plus-test-52
- qald9plus-test-80

### parse-fail (24)

- qald9plus-train-56
- qald9plus-train-115
- qald9plus-test-73
- qald9plus-test-31
- qald9plus-test-22
- qald9plus-test-176
- qald9plus-test-62
- qald9plus-test-139
- qald9plus-test-124
- qald9plus-test-10
- qald9plus-test-178
- qald9plus-test-183
- qald9plus-test-50
- qald9plus-test-102
- qald9plus-test-96
- qald9plus-test-159
- qald9plus-test-144
- qald9plus-test-24
- qald9plus-test-82
- qald9plus-test-201
- qald9plus-test-194
- qald9plus-test-175
- qald9plus-test-78
- qald9plus-test-94

## Statistical power (D-07)

At the kept survivor count (N=514), achieved MDE (alpha=0.05, power=0.80):

| Assumed discordant rate (pi) | achieved_mde |
|---|---|
| 0.2 | 0.0553 |
| 0.25 | 0.0618 |

