"""Out-of-sample validation for scoring algorithms.

Separate from backtest.py on purpose. That harness measures FEATURES, on a
panel the v3.3-v3.10 weights were then tuned against — useful, but in-sample by
construction, and it cannot answer "does this algorithm rank condos better than
that one". This package answers only that, and refuses to answer it in ways
that flatter the algorithm.

Three rules it exists to enforce:

  1. A number is meaningless without a baseline. Every read reports random,
     district-median, and single-feature `psf_vs_dist` alongside the algorithm.
     An algorithm that cannot beat one-feature cheapness has added nothing.
  2. Splits respect time. DEV and VAL are non-overlapping in both their
     feature and outcome windows.
  3. In-regime is not validated. DEV and VAL both sit inside the 2021-2026
     bull market; passing VAL means "not overfit to noise", never "works".
     Only the prospective clock in calibrate_forward.py tests that, and its
     first honest read is ~2027-08.
"""
