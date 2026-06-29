"""Autonomous agent layer.

Independent agents (each on its own model) observe shared state, reason on their
own, and call tools -- run backtests, retrain the ML model, reconcile agent
reliability, and propose BOUNDED changes to the operating scheme. A governor
executes only safe, in-bounds actions and logs everything. Cloudflare Workers AI
is used heavily here: a 70B reasoner, bge embeddings for semantic memory, and a
sentiment classifier.
"""
