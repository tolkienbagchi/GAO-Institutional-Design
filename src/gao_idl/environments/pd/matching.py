"""Who plays whom: fixed pairs or round-robin rematching (circle method).

Stability: internal; it may change without notice. Use the names exported by ``gao_idl`` where
there is one.
"""

from collections.abc import Sequence

from gao_idl.core.errors import ContractError

PAIR_SEPARATOR = "~"


def pair_id(a: str, b: str) -> str:
    """Orientation-free id of a pair: the sorted agent ids joined by ``~``."""
    return PAIR_SEPARATOR.join(sorted((a, b)))


def check_agents(agents: Sequence[str]) -> None:
    """ContractError unless ``agents`` is a non-empty even-sized list of distinct plain ids."""
    if len(agents) < 2 or len(agents) % 2 != 0:
        raise ContractError(
            f"repeated PD needs an even number (>= 2) of agents, got {len(agents)} "
            "(a bye for an odd population is future work)"
        )
    if len(set(agents)) != len(agents):
        raise ContractError("agent ids must be distinct")
    bad = [a for a in agents if not a or PAIR_SEPARATOR in a]
    if bad:
        raise ContractError(f"agent ids must be non-empty and avoid {PAIR_SEPARATOR!r}: {bad}")


def pairs_for_round(
    agents: Sequence[str], matching: str, round_index: int
) -> list[tuple[str, str]]:
    """The ``(row, col)`` pairs of round ``round_index``; pair ``k`` is branch ``pair_k``.

    ``fixed``: ``(agents[0], agents[1]), (agents[2], agents[3]), …`` every round. ``round_robin``:
    circle method — ``agents[0]`` stays, the others rotate by ``round_index`` (mod ``n - 1``), and
    position ``i`` plays position ``n - 1 - i``. Over ``n - 1`` rounds everyone meets everyone once.
    """
    n = len(agents)
    if matching == "fixed":
        return [(agents[2 * k], agents[2 * k + 1]) for k in range(n // 2)]
    if matching == "round_robin":
        rest = list(agents[1:])
        shift = round_index % (n - 1)
        rotated = rest[len(rest) - shift :] + rest[: len(rest) - shift] if shift else rest
        circle = [agents[0], *rotated]
        return [(circle[i], circle[n - 1 - i]) for i in range(n // 2)]
    raise ContractError(f"matching must be 'fixed' or 'round_robin', got {matching!r}")
