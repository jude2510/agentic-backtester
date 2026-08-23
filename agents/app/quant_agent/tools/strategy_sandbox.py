"""
Sandbox for executing LLM-generated Backtrader strategy code.

The strategy text originates from a user's free-form description, so it must be
treated as untrusted input, not as trusted code that happens to be generated.
Without this, `exec` runs it with the full builtins and whatever credentials the
agent container holds in its environment.

Two independent layers, either of which should stop an escape:

  1. Static AST validation — reject disallowed imports, dangerous builtin names,
     and every dunder attribute access before a single line runs. The dunder ban
     is what closes the classic `().__class__.__bases__[0].__subclasses__()`
     route back to arbitrary objects.
  2. Restricted execution namespace — replace `__builtins__` with an explicit
     allowlist, so anything the AST pass missed still has nothing to reach for.

This is defence in depth, not a claim of perfect isolation. Real isolation means
a separate process with no credentials; layering that underneath remains the
stronger long-term move.
"""

from __future__ import annotations

import ast
import builtins as _builtins

__all__ = ["SandboxViolation", "validate_strategy_code", "build_sandbox_globals"]


class SandboxViolation(Exception):
    """Generated strategy code tried to do something outside the sandbox."""


# Modules a legitimate Backtrader strategy could reasonably need.
_ALLOWED_IMPORTS = {
    "backtrader",
    "backtrader.indicators",
    "backtrader.analyzers",
    "math",
    "statistics",
    "datetime",
    "numpy",
    "pandas",
    "collections",
}

# Names that provide code execution, filesystem, or introspection escapes.
# `getattr` is included deliberately: it defeats the dunder-attribute check
# below by letting the string be assembled at runtime.
_FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "open", "input", "breakpoint",
    "__import__", "globals", "locals", "vars", "getattr", "setattr",
    "delattr", "memoryview", "exit", "quit", "help", "dir", "id",
}

# Dunder methods a strategy class may legitimately *define*.
_ALLOWED_DUNDER_DEFS = {"__init__", "__len__", "__repr__", "__str__"}


def _module_root(name: str) -> str:
    return name.split(".")[0]


class _Validator(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[str] = []

    def _reject(self, node: ast.AST, msg: str) -> None:
        line = getattr(node, "lineno", "?")
        self.violations.append(f"line {line}: {msg}")

    # -- imports -----------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name not in _ALLOWED_IMPORTS and _module_root(alias.name) not in _ALLOWED_IMPORTS:
                self._reject(node, f"import of {alias.name!r} is not allowed")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        if mod not in _ALLOWED_IMPORTS and _module_root(mod) not in _ALLOWED_IMPORTS:
            self._reject(node, f"import from {mod!r} is not allowed")
        self.generic_visit(node)

    # -- dangerous names ---------------------------------------------------

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _FORBIDDEN_NAMES:
            self._reject(node, f"use of {node.id!r} is not allowed")
        self.generic_visit(node)

    # -- dunder attribute access (sandbox-escape route) --------------------

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("__") and node.attr.endswith("__"):
            self._reject(node, f"access to dunder attribute {node.attr!r} is not allowed")
        self.generic_visit(node)

    # -- dunder definitions ------------------------------------------------

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        name = node.name
        if name.startswith("__") and name.endswith("__") and name not in _ALLOWED_DUNDER_DEFS:
            self._reject(node, f"defining {name!r} is not allowed")
        self.generic_visit(node)

    # -- misc escapes ------------------------------------------------------

    def visit_Global(self, node: ast.Global) -> None:
        self._reject(node, "`global` is not allowed")
        self.generic_visit(node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self._reject(node, "`nonlocal` is not allowed")
        self.generic_visit(node)


def _strip_fences(code: str) -> str:
    """Remove ```python fences if the model wrapped the code in markdown."""
    text = code.strip()
    if not text.startswith("```"):
        return code
    lines = text.splitlines()
    lines = lines[1:]                      # drop opening fence (+ language tag)
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def validate_strategy_code(code: str) -> str:
    """Validate generated strategy code, returning the cleaned source.

    Raises:
        SandboxViolation: if the code fails to parse or breaks a sandbox rule.
    """
    cleaned = _strip_fences(code)

    try:
        tree = ast.parse(cleaned)
    except SyntaxError as e:
        raise SandboxViolation(f"strategy code does not parse: {e}") from e

    validator = _Validator()
    validator.visit(tree)

    if validator.violations:
        raise SandboxViolation(
            "generated strategy code rejected by sandbox:\n  - "
            + "\n  - ".join(validator.violations)
        )

    return cleaned


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    """`__import__` restricted to the same allowlist the AST pass enforces.

    The `import` statement compiles to a call to `__import__`, so removing it
    from builtins does not merely block malicious imports — it breaks every
    legitimate `import backtrader as bt` too. Supplying a guarded version keeps
    real strategies working while still refusing anything off the allowlist,
    and adds a runtime check behind the static one.
    """
    root = name.split(".")[0]
    if name not in _ALLOWED_IMPORTS and root not in _ALLOWED_IMPORTS:
        raise ImportError(f"import of {name!r} is blocked by the strategy sandbox")
    return _builtins.__import__(name, globals, locals, fromlist, level)


# Builtins a Backtrader strategy actually needs. `__build_class__` is required
# for `class Foo(bt.Strategy):` to compile at all.
_SAFE_BUILTINS = {
    "__build_class__": _builtins.__build_class__,
    "__import__": _guarded_import,
    "__name__": "generated_strategy",
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "divmod": divmod, "enumerate": enumerate, "filter": filter, "float": float,
    "format": format, "frozenset": frozenset, "int": int, "isinstance": isinstance,
    "issubclass": issubclass, "iter": iter, "len": len, "list": list, "map": map,
    "max": max, "min": min, "next": next, "object": object, "pow": pow,
    "print": print, "range": range, "repr": repr, "reversed": reversed,
    "round": round, "set": set, "slice": slice, "sorted": sorted, "str": str,
    "sum": sum, "tuple": tuple, "zip": zip,
    "super": super, "property": property, "staticmethod": staticmethod,
    "classmethod": classmethod, "type": type,
    # Exceptions a strategy might raise or catch.
    "Exception": Exception, "ValueError": ValueError, "TypeError": TypeError,
    "KeyError": KeyError, "IndexError": IndexError, "AttributeError": AttributeError,
    "ZeroDivisionError": ZeroDivisionError, "ArithmeticError": ArithmeticError,
    "RuntimeError": RuntimeError, "StopIteration": StopIteration,
}


def build_sandbox_globals(bt_module) -> dict:
    """Execution namespace for generated strategy code.

    Supplying `__builtins__` explicitly is essential: when the key is absent,
    Python silently inserts the *real* builtins, which is what made the previous
    `exec(strategy_code, {'bt': bt, ...})` call unrestricted.
    """
    return {
        "__builtins__": dict(_SAFE_BUILTINS),
        "bt": bt_module,
        "btind": bt_module.indicators,
        "btanalyzers": getattr(bt_module, "analyzers", None),
    }
