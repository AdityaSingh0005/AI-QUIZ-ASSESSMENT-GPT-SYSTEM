"""Gunicorn production configuration for Render."""
import os

bind = "0.0.0.0:" + os.getenv("PORT", "10000")
worker_class = "gthread"
workers = max(1, int(os.getenv("WEB_CONCURRENCY", "1")))
threads = max(4, int(os.getenv("GUNICORN_THREADS", "8")))
timeout = max(60, int(os.getenv("GUNICORN_TIMEOUT", "300")))
graceful_timeout = 30
keepalive = 5
max_requests = 1000
max_requests_jitter = 100
preload_app = False
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info")
