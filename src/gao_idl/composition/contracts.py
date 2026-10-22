"""Construction-time contract checking and a JSON description of a process tree.

``check`` validates wiring only (port routing, schema identity across connectors, names, exit-port
conflicts). Store keys are *not* ordered statically: scopes are enforced per layer / connector at
runtime (see NOTES.md, M1 task 6).
"""

from typing import Any

from pydantic import BaseModel

from gao_idl.composition.connector import Connector
from gao_idl.composition.loop import Loop
from gao_idl.composition.par import Par
from gao_idl.composition.primitive import Primitive
from gao_idl.composition.process import Process
from gao_idl.composition.seq import Seq
from gao_idl.core.layer import Contract


def check(process: Process) -> None:
    """Recursively validate the wiring of ``process``; raises ContractError on any mismatch.

    Every composite constructor calls this on itself, so a composite that exists is well wired;
    calling it again (for instance in a test) re-checks the whole tree.
    """
    if isinstance(process, Seq | Loop):
        process.validate_wiring()
        process.compute_exits()  # raises on conflicting exit schemas
    elif isinstance(process, Par):
        process.validate_wiring()
    children = getattr(process, "_children", None)  # leaves (Primitive, custom) have none
    if children is not None:
        for _, sub in children():
            check(sub)


def _schema(schema: type[BaseModel]) -> dict[str, Any]:
    return {"name": schema.__name__, "fields": sorted(schema.model_fields)}


def _entry(contract: Contract) -> dict[str, Any]:
    return {
        "state_schema": _schema(contract.state_schema),
        "roles": sorted(contract.roles),
        "store_reads": sorted(contract.store_reads),
        "store_writes": sorted(contract.store_writes),
    }


def _connector(connector: Connector) -> dict[str, Any]:
    return {
        "output_schema": _schema(connector.output_schema),
        "store_reads": sorted(connector.store_reads),
        "store_writes": sorted(connector.store_writes),
    }


def describe(process: Process) -> dict[str, Any]:
    """A JSON-able tree: kind, name, entry contract, exit ports, and the structure below it."""
    node: dict[str, Any] = {
        "name": process.name,
        "entry": _entry(process.entry),
        "exits": {str(port): _schema(schema) for port, schema in process.exits.items()},
    }
    if isinstance(process, Primitive):
        node["kind"] = "primitive"
        node["timeout"] = process.layer.timeout.kind
    elif isinstance(process, Seq):
        node["kind"] = "seq"
        node["first"] = describe(process.first)
        node["routes"] = {
            str(port): {"connector": _connector(conn), "then": describe(cont)}
            for port, (conn, cont) in sorted(process.routes.items())
        }
    elif isinstance(process, Par):
        node["kind"] = "par"
        node["splitter"] = {
            "type": type(process.split).__name__,
            "input_schema": _schema(process.split.input_schema),
        }
        node["joiner"] = {
            "type": type(process.join).__name__,
            "exits": {str(p): _schema(s) for p, s in process.join.exits.items()},
        }
        node["branches"] = {name: describe(proc) for name, proc in sorted(process.branches.items())}
    elif isinstance(process, Loop):
        node["kind"] = "loop"
        node["max_iter"] = process.max_iter
        node["on_limit"] = process.on_limit
        node["until"] = {str(p): str(t) for p, t in sorted(process.until.items())}
        node["feedback"] = {str(p): _connector(c) for p, c in sorted(process.feedback.items())}
        node["body"] = describe(process.body)
    else:
        node["kind"] = "process"
    return node
