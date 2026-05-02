"""Command-line entry points."""

from . import app as _app

__all__ = [name for name in dir(_app) if not name.startswith("_")]
globals().update({name: getattr(_app, name) for name in __all__})
