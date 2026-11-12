"""Edge-biased scripts: many runs of an environment that lean on its boundaries.

``edge_scripts`` plays an environment with actions sampled from each decision's JSON Schema,
biased toward the places where readings of a rule come apart: the smallest and largest allowed
number, zero, a number another participant just chose (a tie), a number the participant was
just shown (bidding exactly the reserve), an empty selection, and not acting at all. The scripts
are data (``play.ActionScript``), so the same ones can be replayed on another implementation
(``diverge``) or checked against a spec (``invariants``).

Stability: supporting API: documented in the API reference, but not re-exported by
``gao_idl``; it may change in a minor release before 1.0 (see NOTES.md).
"""

from typing import Any

import numpy as np

from gao_idl.authoring.play import MAX_DECISIONS, ActionScript
from gao_idl.core.errors import GaoError
from gao_idl.core.ids import AgentId
from gao_idl.runtime.session import Session

ATTEMPTS = 24  # samples tried per decision before the participant is made to stay silent
SILENT_SCRIPTS = 0.2  # share of scripts in which participants may fail to act
SILENT_RATE = 0.1  # chance of not acting, per decision, in such a script


def _numbers(value: Any, into: list[float]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, int | float):
        into.append(float(value))
    elif isinstance(value, dict):
        for inner in value.values():
            _numbers(inner, into)
    elif isinstance(value, list):
        for inner in value:
            _numbers(inner, into)


class _Sampler:
    """Draws one value for a JSON Schema; ``pool`` holds numbers worth colliding with."""

    def __init__(self, root: dict[str, Any], rng: np.random.Generator, pool: list[float]) -> None:
        self.root = root
        self.rng = rng
        self.pool = pool

    def _resolve(self, schema: dict[str, Any]) -> dict[str, Any]:
        seen = 0
        while "$ref" in schema and seen < 20:
            node: Any = self.root
            for part in schema["$ref"].removeprefix("#/").split("/"):
                node = node[part]
            schema = {**node, **{k: v for k, v in schema.items() if k != "$ref"}}
            seen += 1
        if len(schema.get("allOf", [])) == 1:
            schema = {**self._resolve(schema["allOf"][0]), **schema}
        return schema

    def value(self, schema: dict[str, Any], plain: bool) -> Any:
        """A value for ``schema``; ``plain`` asks for the least adventurous one."""
        schema = self._resolve(schema)
        for key in ("anyOf", "oneOf"):
            if key in schema:
                options = [self._resolve(o) for o in schema[key]]
                if plain:
                    nulls = [o for o in options if o.get("type") == "null"]
                    return None if nulls else self.value(options[0], plain)
                return self.value(options[int(self.rng.integers(len(options)))], plain)
        if "const" in schema:
            return schema["const"]
        if "enum" in schema:
            return schema["enum"][0 if plain else int(self.rng.integers(len(schema["enum"])))]
        kind = schema.get("type")
        if isinstance(kind, list):
            kind = next((k for k in kind if k != "null"), "null")
        if kind == "null":
            return None
        if kind == "boolean":
            return False if plain else bool(self.rng.integers(2))
        if kind in ("number", "integer"):
            return self._number(schema, kind == "integer", plain)
        if kind == "string":
            return self._string(schema)
        if kind == "array":
            return self._array(schema, plain)
        if kind == "object" or "properties" in schema:
            return self._object(schema, plain)
        return {}

    def _number(self, schema: dict[str, Any], whole: bool, plain: bool) -> float | int:
        step = 1.0 if whole else 1e-6
        low = schema.get("minimum")
        high = schema.get("maximum")
        if schema.get("exclusiveMinimum") is not None:
            low = schema["exclusiveMinimum"] + step
        if schema.get("exclusiveMaximum") is not None:
            high = schema["exclusiveMaximum"] - step

        def fits(x: float) -> bool:
            return (low is None or x >= low) and (high is None or x <= high)

        if plain:
            picked = low if low is not None else (0.0 if fits(0.0) else high)
        else:
            edges = [x for x in (low, high, 0.0) if x is not None and fits(x)]
            near = [x for x in self.pool if fits(x)]
            roll = self.rng.random()
            if roll < 0.3 and edges:
                picked = edges[int(self.rng.integers(len(edges)))]
            elif roll < 0.65 and near:
                picked = near[int(self.rng.integers(len(near)))]
            else:
                top = high if high is not None else max([10.0, *near]) * 1.5
                bottom = low if low is not None else min([0.0, *near])
                picked = float(self.rng.uniform(bottom, max(top, bottom)))
                picked = round(picked, 2) if fits(round(picked, 2)) else picked
        picked = 0.0 if picked is None else float(picked)
        if whole:
            return int(round(picked))
        return picked

    def _string(self, schema: dict[str, Any]) -> str:
        low = int(schema.get("minLength", 0))
        high = int(schema.get("maxLength", max(low, 3)))
        text = f"x{int(self.rng.integers(3))}"
        return (text + "x" * low)[: max(low, min(high, len(text)))]

    def _array(self, schema: dict[str, Any], plain: bool) -> list[Any]:
        items = self._resolve(schema.get("items", {}))
        low = int(schema.get("minItems", 0))
        choices = items.get("enum")
        if choices is None and "anyOf" in items:
            consts = [self._resolve(o).get("const") for o in items["anyOf"]]
            choices = consts if all(c is not None for c in consts) else None
        cap = len(choices) if choices is not None else low + 3
        high = int(schema.get("maxItems", cap))
        high = max(low, min(high, cap)) if schema.get("uniqueItems") or choices else max(low, high)
        if plain:
            size = low
        else:
            roll = self.rng.random()
            size = (
                low
                if roll < 0.25
                else high
                if roll < 0.4
                else int(self.rng.integers(low, high + 1))
            )
        if choices is not None and (schema.get("uniqueItems") or size <= len(choices)):
            order = self.rng.permutation(len(choices))[:size]
            return [choices[int(i)] for i in sorted(order)]
        return [self.value(items, plain) for _ in range(size)]

    def _object(self, schema: dict[str, Any], plain: bool) -> dict[str, Any]:
        out: dict[str, Any] = {}
        required = set(schema.get("required", []))
        for name, sub in schema.get("properties", {}).items():
            if name in required or (not plain and self.rng.random() < 0.7):
                out[name] = self.value(sub, plain)
        return out


def sample_action(
    schema: dict[str, Any], rng: np.random.Generator, pool: list[float], plain: bool = False
) -> Any:
    """One action for a JSON Schema, biased to its edges and to the numbers in ``pool``.

    Handles ``$ref``/``$defs``, ``anyOf``/``oneOf`` (including optional objects), enums, bounded
    and unbounded numbers, arrays of choices and nested objects. ``plain`` returns the least
    adventurous value (lower bounds, empty selections, optional parts left out).
    """
    return _Sampler(schema, rng, pool).value(schema, plain)


def edge_scripts(env_ref: str, params: dict[str, Any], seed: int, n: int) -> list[ActionScript]:
    """``n`` scripts for the environment, each a full run with edge-biased actions.

    Every script was produced by playing a session started with ``seed``, so
    ``play(env_ref, params, seed, script)`` reproduces it. Script ``i`` draws from a generator
    seeded with ``(seed, i)``: same arguments, same scripts. An action the environment rejects is
    re-drawn, up to ``ATTEMPTS`` times and more cautiously each time; if none is accepted the
    participant stays silent. In one script in five, participants also fail to act with
    probability 0.1 per decision; the others have no deliberate silence, so that an environment
    whose rule is "the run stops" is still explored past its first step.
    """
    scripts: list[ActionScript] = []
    for i in range(n):
        rng = np.random.default_rng([seed, i])
        silent = SILENT_RATE if rng.random() < SILENT_SCRIPTS else 0.0
        session = Session.start(env_ref, params, seed)
        script: ActionScript = {}
        decisions = 0
        session.advance()
        while not session.finished:
            pending = sorted(session.pending(), key=lambda r: str(r.decision_id))
            if not pending:
                raise RuntimeError("the session is waiting but no decision is pending")
            chosen: list[float] = []  # numbers picked in this batch: the next participant may tie
            for request in pending:
                decisions += 1
                if decisions > MAX_DECISIONS:
                    raise RuntimeError(
                        f"more than {MAX_DECISIONS} decisions; is the run unbounded?"
                    )
                entries = script.setdefault(str(request.agent_id), [])
                action: dict[str, Any] | None = None
                if rng.random() >= silent:
                    pool = list(chosen)
                    _numbers(request.observation, pool)
                    for attempt in range(ATTEMPTS):
                        plain = attempt >= ATTEMPTS - 2
                        draw = sample_action(request.action_schema, rng, pool, plain)
                        if not isinstance(draw, dict):
                            continue
                        try:
                            session.submit(
                                AgentId(str(request.agent_id)), request.decision_id, draw
                            )
                        except GaoError:
                            continue
                        action = draw
                        break
                if action is None:
                    session.timeout(request.decision_id)
                else:
                    _numbers(action, chosen)
                entries.append(action)
            session.advance()
        scripts.append(script)
    return scripts
