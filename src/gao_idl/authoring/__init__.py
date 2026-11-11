"""Verified authoring: check that a built environment is the institution its designer described.

Four deterministic pieces, none of which calls a language model: a structured ``EnvSpec`` of the
designer's intent (``spec``), runs described by data (``play``), a diagram and a who-knows-what
table generated from the built process and its runs (``render``), a mechanical comparison of the
two (``check``), and worked scenarios a person confirms or rejects (``cards``).

These tools check that the code matches the designer's intent. They say nothing about whether
the intended institution is an adequate model of a real one.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

from gao_idl.authoring.cards import (
    Card,
    CardExpect,
    freeze,
    load_cards,
    render_card,
    run_card,
    save_cards,
    verify_cards,
)
from gao_idl.authoring.check import Mismatch, Run, check_spec
from gao_idl.authoring.play import ActionScript, play, script_of
from gao_idl.authoring.render import (
    KnowledgeRow,
    KnowledgeTable,
    knowledge_table,
    render_design,
    to_mermaid,
    to_outline,
)
from gao_idl.authoring.spec import (
    Assumption,
    EnvSpec,
    InvariantSpec,
    PayoffSpec,
    RepeatSpec,
    RoleSpec,
    SecretSpec,
    StepSpec,
    load_spec,
    role_kind,
)

__all__ = [
    "ActionScript",
    "Assumption",
    "Card",
    "CardExpect",
    "EnvSpec",
    "InvariantSpec",
    "KnowledgeRow",
    "KnowledgeTable",
    "Mismatch",
    "PayoffSpec",
    "RepeatSpec",
    "RoleSpec",
    "Run",
    "SecretSpec",
    "StepSpec",
    "check_spec",
    "freeze",
    "knowledge_table",
    "load_cards",
    "load_spec",
    "play",
    "render_card",
    "render_design",
    "role_kind",
    "run_card",
    "save_cards",
    "script_of",
    "to_mermaid",
    "to_outline",
    "verify_cards",
]
