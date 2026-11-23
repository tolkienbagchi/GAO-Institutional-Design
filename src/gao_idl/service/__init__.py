"""The library service: a small web service around the engine for people with only a browser.

``library`` reads the administrator's library file (the only source of environments and
policies); ``casts`` holds small scripted policies for it; ``store`` keeps users, runs and
verdicts in SQLite; ``runs`` plays a library entry with a cast, within caps; ``pages`` writes the
service's own HTML pages; ``app`` is the WSGI application. The service uses the standard library
only, calls no language model and never imports anything a client names.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

from gao_idl.service.library import (
    Cast,
    Entry,
    Library,
    ParamSpec,
    load_library,
    resolve_params,
)

__all__ = ["Cast", "Entry", "Library", "ParamSpec", "load_library", "resolve_params"]
