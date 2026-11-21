"""Static policy for model-written render scripts: an AST allowlist checked before anything runs.

This is the first of several layers and the weakest; a Python-level filter can be argued around. The real boundary
is where the code runs: a separate pod with no network, no secrets, no data volume, a read-only root filesystem,
resource limits and a wall-clock kill. The policy's job is to refuse the obvious early and to tell the model, in
words it can act on, what the sandbox does not allow.
"""

from __future__ import annotations

import ast

ALLOWED_MODULES = frozenset({
    "numpy", "matplotlib", "mpl_toolkits", "PIL", "math", "cmath", "random", "colorsys", "itertools", "functools",
    "collections", "fractions", "decimal", "statistics", "operator", "typing", "dataclasses",
})

# Builtins that reach the interpreter, the filesystem or the terminal.
FORBIDDEN_NAMES = frozenset({
    "eval", "exec", "compile", "__import__", "open", "input", "breakpoint", "globals", "locals", "vars", "getattr",
    "setattr", "delattr", "memoryview", "help", "exit", "quit", "__builtins__", "__loader__", "__spec__", "__file__",
    "os", "sys", "subprocess", "socket", "shutil", "pathlib", "importlib", "ctypes", "builtins", "io", "pickle",
})

# Attributes that lead from an allowed object to file reading, other modules or the interpreter.
FORBIDDEN_ATTRS = frozenset({
    "os", "sys", "subprocess", "socket", "shutil", "pathlib", "importlib", "ctypes", "ctypeslib", "builtins", "io",
    "pickle", "marshal", "mmap", "multiprocessing", "threading", "asyncio", "posix", "system", "popen", "fork",
    "kill", "execv", "execve", "spawn", "load", "loadtxt", "genfromtxt", "fromfile", "memmap", "DataSource",
    "f2py", "distutils", "testing", "open", "imread", "frombuffer_file", "get_sample_data", "urlopen", "urlretrieve",
    "f_globals", "f_locals", "f_back", "f_code", "gi_frame", "cr_frame", "tb_frame", "func_globals", "co_code",
})

ALLOWED_DUNDER_NAMES = frozenset({"__name__"})

INTERNAL_STRINGS = ("__class__", "__bases__", "__mro__", "__subclasses__", "__globals__", "__builtins__",
                    "__import__", "__code__", "__closure__", "__dict__", "__getattribute__", "__reduce__")

MAX_CODE_CHARS = 20000


class _Checker(ast.NodeVisitor):
    def __init__(self) -> None:
        self.found: list[tuple[int, str]] = []

    def add(self, node: ast.AST, what: str) -> None:
        self.found.append((int(getattr(node, "lineno", 0) or 0), what))

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name.split(".", 1)[0] not in ALLOWED_MODULES:
                self.add(node, f"import of '{alias.name}' is not allowed")
            for part in alias.name.split(".")[1:]:
                if part in FORBIDDEN_ATTRS or part.startswith("_"):
                    self.add(node, f"import of '{alias.name}' is not allowed")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            self.add(node, "relative imports are not allowed")
        elif (node.module or "").split(".", 1)[0] not in ALLOWED_MODULES:
            self.add(node, f"import from '{node.module}' is not allowed")
        for alias in node.names:
            if alias.name in FORBIDDEN_ATTRS or (alias.name.startswith("_") and alias.name != "*"):
                self.add(node, f"importing '{alias.name}' is not allowed")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in FORBIDDEN_NAMES:
            self.add(node, f"'{node.id}' is not allowed")
        elif node.id.startswith("_") and node.id != "_" and node.id not in ALLOWED_DUNDER_NAMES:
            self.add(node, f"the name '{node.id}' (names beginning with an underscore are not allowed)")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("_"):
            self.add(node, f"the attribute '{node.attr}' (attributes beginning with an underscore are not allowed)")
        elif node.attr in FORBIDDEN_ATTRS:
            self.add(node, f"the attribute '{node.attr}' (it reads files or reaches a blocked module)")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and any(s in node.value for s in INTERNAL_STRINGS):
            self.add(node, "a string naming interpreter internals")

    def _def(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        if node.name in FORBIDDEN_NAMES or (node.name.startswith("__") and node.name not in ("__init__", "__call__")):
            self.add(node, f"defining '{node.name}' is not allowed")
        self.generic_visit(node)

    visit_FunctionDef = _def
    visit_AsyncFunctionDef = _def
    visit_ClassDef = _def


def check(code: str) -> list[str]:
    """Policy violations of `code`, in source order (empty: it may run). A syntax error is not a violation: the
    interpreter reports it with its line, which is more useful to the model."""
    if len(code) > MAX_CODE_CHARS:
        return [f"the script is {len(code)} characters; the limit is {MAX_CODE_CHARS}"]
    try:
        tree = ast.parse(code, mode="exec")
    except (SyntaxError, ValueError):
        return []
    checker = _Checker()
    checker.visit(tree)
    seen: set[tuple[int, str]] = set()
    out: list[str] = []
    for line, what in sorted(checker.found):
        if (line, what) not in seen:
            seen.add((line, what))
            out.append(f"line {line}: {what}")
    return out


def describe(violations: list[str], limit: int = 5) -> str:
    more = f" (and {len(violations) - limit} more)" if len(violations) > limit else ""
    return ("Sandbox policy: " + "; ".join(violations[:limit]) + more + ". Nothing ran. Only numpy, matplotlib, PIL and "
            "the standard maths modules may be imported; no files may be read; write out.png or frames/frame_NNN.png.")
