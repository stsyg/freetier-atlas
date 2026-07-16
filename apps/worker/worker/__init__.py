"""FreeTier Atlas worker and scheduler package.

Slice 2 of the F002 application scaffold: a Python worker and scheduler that
operate a real PostgreSQL-backed job queue. The scheduler enqueues heartbeat
jobs on an interval; the worker atomically claims and completes them. Both
services publish a database-backed liveness heartbeat used by their Docker
health checks. The real source adapters, extraction, verification, and
publication logic arrive in later features (F004+).
"""

__all__ = ["__version__"]

__version__ = "0.1.0.dev0"
