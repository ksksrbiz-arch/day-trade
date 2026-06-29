"""Self-improving ML layer.

A calibrated model learns, from the survivorship-bias-reduced CRSP-lite
universe, which technical/quant setups precede positive forward returns. Its
output (ml_score in [-1,1]) becomes a fifth voice in alpha.confluence().

Design guarantees:
  * No train/serve skew -- features.feature_vector() is the single source of
    feature math used both to build the training set and to score live.
  * No lookahead -- training uses a time-ordered split; labels are strictly
    forward returns.
  * Safe continuous improvement -- train.py runs champion/challenger: a freshly
    trained model only replaces the incumbent if it beats it on held-out AUC.
  * Dependency-light -- pure NumPy logistic regression (no sklearn), so the
    24/7 retrain daemon has no ABI/version fragility. Coefficients double as
    interpretable feature importances.
"""

from .features import feature_vector, FEATURES  # noqa: F401
