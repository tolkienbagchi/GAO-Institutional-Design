"""Sanity environments for the learners: a Bernoulli bandit and a deterministic chain."""

from gao_idl.environments.toy.bandit import build_bandit
from gao_idl.environments.toy.chain import build_chain

__all__ = ["build_bandit", "build_chain"]
