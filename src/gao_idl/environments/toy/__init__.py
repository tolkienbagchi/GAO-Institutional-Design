"""Sanity environments for the learners: a Bernoulli bandit and a deterministic chain.

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

from gao_idl.environments.toy.bandit import build_bandit
from gao_idl.environments.toy.chain import build_chain

__all__ = ["build_bandit", "build_chain"]
