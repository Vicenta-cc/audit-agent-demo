"""Compatibility entry point; the crawler owns the single scheduler implementation."""
from .crawler_browser import load_request_scheduler


def RequestScheduler(*args, **kwargs):
    return load_request_scheduler().RequestScheduler(*args, **kwargs)
