"""F007 Slice 5a: the permanent negative-security adversarial regression corpus.

This package consolidates and extends the security guarantees of the F007
"llm-download-admin" subsystem (S1 LLM intake, S2 abuse controls, S3 ZIP export,
S4 private admin) into one durable, offline suite so future changes cannot
silently regress them. Every test here is deterministic and offline -- a fake
LLM adapter, a mock GitHub OAuth double, in-memory stores, and pure validators;
no real network, no live database, no real provider.
"""
