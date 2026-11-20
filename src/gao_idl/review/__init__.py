"""The review page: one self-contained HTML file in which a designer checks an environment.

``draw`` makes the pictures (SVG, in Python); ``bundle`` gathers everything the page shows into
one JSON document, the only contract between the engine and any front end; ``page`` writes the
HTML file; ``verdicts`` reads the verdicts a person gave on the page and applies them to the
cards and the spec. Nothing here calls a language model or records a verdict by itself.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

from gao_idl.review.bundle import (
    Bundle,
    CardView,
    CheckStatus,
    dump_bundle,
    load_bundle,
    make_bundle,
    outcome_hash,
    save_bundle,
)
from gao_idl.review.draw import flow_svg, timeline_svg

__all__ = [
    "Bundle",
    "CardView",
    "CheckStatus",
    "dump_bundle",
    "flow_svg",
    "load_bundle",
    "make_bundle",
    "outcome_hash",
    "save_bundle",
    "timeline_svg",
]
