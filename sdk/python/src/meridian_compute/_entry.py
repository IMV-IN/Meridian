"""Entry-point rules (spec section 7.2): which functions may be tasks, and where the project root is."""

from __future__ import annotations

import inspect
import sys
import types
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import _protocol as p
from ._errors import EntryPointError


@dataclass(frozen=True)
class Entry:
    module: str
    qualname: str
    root: Path  # R: the sys.path entry the top-level module was imported from


def _find_root(module: str, file: Path) -> Path:
    """The directory above the top-level package or module, checked against the module path."""
    parts = module.split(".")
    if file.name == "__init__.py":
        here, names = file.parent, parts
    else:
        if file.stem != parts[-1]:
            raise EntryPointError("cannot locate the project root of module %r (file %s)" % (module, file))
        here, names = file.parent, parts[:-1]
    for name in reversed(names):
        if here.name != name:
            raise EntryPointError("cannot locate the project root of module %r (file %s)" % (module, file))
        here = here.parent
    return here


def resolve_entry(fn: Callable[..., Any]) -> Entry:
    """Validate `fn` as a task entry point and locate its project root. Raises EntryPointError."""
    if not isinstance(fn, types.FunctionType):
        raise EntryPointError("a task must be a plain function defined with def at module level")
    qualname = fn.__qualname__
    if "<lambda>" in qualname:
        raise EntryPointError("a lambda cannot be a task: use a def at module level")
    if "<locals>" in qualname:
        raise EntryPointError("a nested function cannot be a task: move it to module level")
    if "." in qualname:
        raise EntryPointError("a method cannot be a task in phase 1: use a module-level function")
    if inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn) or inspect.isgeneratorfunction(fn):
        raise EntryPointError("async functions and generators cannot be tasks in phase 1: use a plain def")
    module = fn.__module__
    if module == "__main__":
        main = sys.modules.get("__main__")
        main_file = getattr(main, "__file__", None)
        if not main_file:
            raise EntryPointError("define the task in a .py file; the REPL, python -c and notebooks are not supported")
        path = Path(main_file).resolve()
        module = path.stem
        if "." in module or not p.valid_entry_name(module):
            raise EntryPointError(
                "the file name %r is not a valid module name for a task; rename it to letters, digits and underscores"
                % path.name
            )
        root = path.parent
    else:
        top = module.split(".")[0]
        mod = sys.modules.get(module)
        file_name = getattr(mod, "__file__", None)
        if not file_name:
            raise EntryPointError("module %r has no file; define the task in a .py file of your project" % module)
        top_mod = sys.modules.get(top)
        paths = list(getattr(top_mod, "__path__", []) or [])
        if getattr(top_mod, "__file__", None) is None and len(paths) > 1:
            raise EntryPointError("%r is a namespace package with several locations; use a regular package" % top)
        path = Path(file_name).resolve()
        if any(part in ("site-packages", "dist-packages") for part in path.parts):
            raise EntryPointError(
                "the task must be defined in your project, not an installed package (%s)" % path.parent.name
            )
        root = _find_root(module, path)
    if not p.valid_entry_name(module) or not p.valid_entry_name(qualname):
        raise EntryPointError(
            "module %r or function %r is not a valid entry name (ASCII letters, digits, underscores; "
            "no dunder parts)" % (module, qualname)
        )
    return Entry(module=module, qualname=qualname, root=root)
