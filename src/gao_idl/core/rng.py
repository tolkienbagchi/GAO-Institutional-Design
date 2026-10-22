"""Deterministic, order-independent random streams keyed by (seed, path, purpose).

Stability: internal; it may change without notice. Use the names exported by ``gao_idl`` where
there is one.
"""

import hashlib

import numpy as np
from numpy.random import Generator

from gao_idl.core.paths import Path, path_str


def stream(root_seed: int, path: Path, purpose: str) -> Generator:
    """Return a fresh generator for ``(root_seed, path, purpose)``, independent of other streams."""
    key = hashlib.blake2b(f"{path_str(path)}|{purpose}".encode(), digest_size=8).digest()
    seq = np.random.SeedSequence(root_seed, spawn_key=(int.from_bytes(key, "big"),))
    return np.random.default_rng(seq)
