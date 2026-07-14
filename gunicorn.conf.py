"""Gunicorn configuration.

Plan generation is CPU-heavy (interpolation, contouring, PDF rendering,
DWG conversion), so requests can take minutes: keep a generous timeout.

Workers are recycled after a bounded number of requests. Numpy/scipy/ezdxf
allocate large buffers per request and CPython rarely returns that memory
to the OS, so resident memory creeps up over time; recycling keeps the
process from eventually exhausting the machine's memory.
"""

import os

bind = f"0.0.0.0:{os.getenv('PORT', '8080')}"
workers = int(os.getenv("WEB_CONCURRENCY", "1"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "300"))
graceful_timeout = 60

max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "50"))
max_requests_jitter = int(os.getenv("GUNICORN_MAX_REQUESTS_JITTER", "10"))

accesslog = "-"
errorlog = "-"
