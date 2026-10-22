"""Loading ``gao_idl`` modules for inspection without side effects on the package.

``gao_idl.run`` is a command-line module, but ``gao_idl`` also exports a function called ``run``;
*importing* the module would rebind the package attribute to the module. Tests that walk every
module therefore read entry points from source instead of importing them.
"""

import ast
import importlib
import importlib.util
import types

ENTRY_POINTS = frozenset({"gao_idl.run", "gao_idl.replay"})


def load_module(name: str) -> types.ModuleType:
    """The imported module, or for an entry point a stand-in that carries its docstring."""
    if name not in ENTRY_POINTS:
        return importlib.import_module(name)
    spec = importlib.util.find_spec(name)
    assert spec is not None and spec.origin is not None, name
    with open(spec.origin) as f:
        doc = ast.get_docstring(ast.parse(f.read()))
    return types.ModuleType(name, doc)
