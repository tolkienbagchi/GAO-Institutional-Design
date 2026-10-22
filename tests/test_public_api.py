"""The public surface is deliberate: additions to ``gao_idl.__all__`` or ``gao_idl.llm.__all__``
must change the lists below, and every module must say how stable it is."""

import dataclasses
import inspect
import os
import pkgutil
import re
import subprocess
import sys
import types
from typing import Any

import pytest

import gao_idl
import gao_idl.llm
from tests.modules import load_module

EXPECTED = [
    "Session",
    "run",
    "run_sync",
    "RunnerPolicy",
    "SimpleLayer",
    "Contract",
    "Config",
    "Primitive",
    "Seq",
    "Par",
    "Loop",
    "Connector",
    "DisjointSplitter",
    "DictJoiner",
    "Fail",
    "DefaultAction",
    "DropRole",
    "AgentAdapter",
    "RandomAgent",
    "ScriptedAgent",
    "HumanTransportAgent",
    "TableAgent",
    "EpisodeContext",
]
NOT_EXPORTED_BUT_PUBLIC = {"selftest"}  # task 1 requires gao_idl.selftest(); see NOTES.md

EXPECTED_LLM = {
    "AnthropicBackend",
    "BackendError",
    "CallRecord",
    "CodecParseError",
    "DecisionCodec",
    "DefaultCodec",
    "DefaultReportRenderer",
    "FullTranscript",
    "LLMAgent",
    "LLMBackend",
    "Memory",
    "Message",
    "MockBackend",
    "MockCall",
    "MockExhausted",
    "NoMemory",
    "OpenAICompatBackend",
    "RepairPolicy",
    "ReplayBackend",
    "ReplayExhausted",
    "ReportRenderer",
    "Response",
    "Window",
    "to_anthropic_schema",
}


def test_the_export_list_is_exactly_the_documented_one() -> None:
    assert len(gao_idl.__all__) == len(set(gao_idl.__all__)) == len(EXPECTED) == 23
    assert set(gao_idl.__all__) == set(EXPECTED)


def test_star_import_and_attributes_match_the_export_list() -> None:
    namespace: dict[str, Any] = {}
    exec("from gao_idl import *", namespace)
    assert {k for k in namespace if k != "__builtins__"} == set(EXPECTED)
    public_attributes = {
        name
        for name, value in vars(gao_idl).items()
        if not name.startswith("_") and not isinstance(value, types.ModuleType)
    }
    assert public_attributes == set(EXPECTED) | NOT_EXPORTED_BUT_PUBLIC


def test_llm_symbols_are_exported_from_gao_idl_llm_only() -> None:
    assert set(gao_idl.llm.__all__) == EXPECTED_LLM
    assert len(gao_idl.llm.__all__) == len(EXPECTED_LLM)
    for name in EXPECTED_LLM:
        assert getattr(gao_idl.llm, name) is not None
        assert name not in gao_idl.__all__ and not hasattr(gao_idl, name)


def test_importing_gao_idl_does_not_import_the_llm_package() -> None:
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    code = "import sys, gao_idl; assert 'gao_idl.llm' not in sys.modules, 'llm imported'"
    subprocess.run([sys.executable, "-c", code], check=True, env=env)


def _own_public_members(cls: type) -> list[str]:
    """Public methods, properties and class/static methods defined in ``cls`` itself."""
    fields = {f.name for f in dataclasses.fields(cls)} if dataclasses.is_dataclass(cls) else set()
    out = []
    for name, value in vars(cls).items():
        if name.startswith("_") or name in fields:
            continue
        if isinstance(value, staticmethod | classmethod):
            value = value.__func__
        elif isinstance(value, property):
            value = value.fget
        if inspect.isfunction(value):
            out.append(name)
    return out


@pytest.mark.parametrize("name", EXPECTED + sorted(EXPECTED_LLM))
def test_every_public_symbol_is_documented(name: str) -> None:
    obj = getattr(gao_idl, name, None) or getattr(gao_idl.llm, name)
    assert (obj.__doc__ or "").strip(), f"{name} has no docstring of its own"
    if inspect.isclass(obj):
        missing = []
        for member in _own_public_members(obj):
            value = inspect.getattr_static(obj, member)
            if isinstance(value, staticmethod | classmethod):
                value = value.__func__
            elif isinstance(value, property):
                value = value.fget
            if not (getattr(value, "__doc__", None) or "").strip():
                missing.append(member)
        assert not missing, f"{name}: public members without a docstring: {missing}"


def _all_modules() -> list[types.ModuleType]:
    modules = [gao_idl]
    for info in pkgutil.walk_packages(gao_idl.__path__, prefix="gao_idl."):
        modules.append(load_module(info.name))
    return modules


def _stability(module: types.ModuleType) -> str:
    doc = " ".join((module.__doc__ or "").split())
    assert "Stability:" in doc, f"{module.__name__} does not say how stable it is"
    return doc.split("Stability:", 1)[1]


def test_every_module_declares_its_stability() -> None:
    modules = _all_modules()
    assert len(modules) > 60
    for module in modules:
        text = _stability(module)
        assert re.search(r"public|supporting|internal", text), module.__name__


def test_stability_notes_name_exactly_the_exported_symbols() -> None:
    modules = [m for m in _all_modules() if m.__name__ not in ("gao_idl", "gao_idl.llm")]
    exported_here: dict[str, set[str]] = {}
    for name in gao_idl.__all__:
        exported_here.setdefault(getattr(gao_idl, name).__module__, set()).add(name)
    for name in gao_idl.llm.__all__:
        exported_here.setdefault(getattr(gao_idl.llm, name).__module__, set()).add(name)
    for module in modules:
        text = _stability(module)
        mentioned = set(re.findall(r"``(\w+)``", text)) - {"gao_idl"}
        assert mentioned == exported_here.get(module.__name__, set()), module.__name__
