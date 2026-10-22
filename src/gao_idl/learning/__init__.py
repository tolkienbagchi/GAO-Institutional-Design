"""Tabular learning: codecs, an ε-greedy Q-learning adapter, and a trainer/evaluator.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

from gao_idl.learning.codecs import ActCodec, FieldObsCodec, ObsCodec, SchemaActCodec
from gao_idl.learning.opponents import OpponentFactory, default_opponent_factory
from gao_idl.learning.qlearning import QConfig, RewardExtractor, TableAgent, Transition
from gao_idl.learning.trainer import EvalResult, TrainResult, TrainSpec, evaluate, train

__all__ = [
    "ActCodec",
    "EvalResult",
    "FieldObsCodec",
    "ObsCodec",
    "OpponentFactory",
    "QConfig",
    "RewardExtractor",
    "SchemaActCodec",
    "TableAgent",
    "TrainResult",
    "TrainSpec",
    "Transition",
    "default_opponent_factory",
    "evaluate",
    "train",
]
