"""Prediction layer.

Turns hypotheses (primarily WallStreetBets chatter, also news) into structured,
ranked, *watched* predictions, resolves them against reality, and grows a
self-indexing DECISION MATRIX of which kinds of predictions actually come true.
That matrix lets the platform estimate the probability an outcome will happen
from the data available -- foresight it feeds back into the ML and execution
layers. Idempotent: re-ingesting the same post never double-counts.
"""
from .store import (connect, record_prediction, resolve_due, matrix_score,  # noqa: F401
                    rebuild_matrix, predictions, decision_matrix)
