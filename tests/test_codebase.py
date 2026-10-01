"""The codebase keeps its own rules: every source file opens with a header saying what it holds, no function is
dead, and no function body is copied from one file into another (shared code is imported from its one home)."""
import ast
import re
import unittest
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sorted([ROOT / "app.py", *(ROOT / "ops").rglob("*.py"), *(ROOT / "tests").rglob("*.py")])
JS = sorted((ROOT / "web" / "js").rglob("*.js"))
PAGE_CONTRACT = {"render", "unmount"}      # docs/CONVENTIONS.md: every page module exports its own
JS_FN = re.compile(r"^(export )?(?:async )?function (\w+)\(([^)]*)\)\s*\{", re.M)


def rel(p):
    return str(p.relative_to(ROOT))


def module_name(p):
    return rel(p)[:-3].replace("/", ".").removesuffix(".__init__")


def js_functions(src):
    """(name, exported, params + body) for each top-level function declaration."""
    for m in JS_FN.finditer(src):
        i, depth = m.end() - 1, 0
        for j in range(i, len(src)):
            depth += {"{": 1, "}": -1}.get(src[j], 0)
            if depth == 0:
                break
        yield m.group(2), bool(m.group(1)), m.group(3).strip() + src[i:j + 1]


class Headers(unittest.TestCase):
    def test_every_source_file_says_what_it_holds(self):
        missing = [rel(p) for p in PY if p.read_text().strip() and not ast.get_docstring(ast.parse(p.read_text()))]
        missing += [rel(p) for p in JS if not p.read_text().lstrip().startswith(("//", "/*"))]
        self.assertTrue((ROOT / "ops" / "schema.sql").read_text().startswith("--"))
        self.assertTrue((ROOT / "web" / "css" / "app.css").read_text().startswith("/*"))
        self.assertEqual(missing, [])


class DeadCode(unittest.TestCase):
    def test_every_python_function_is_used(self):
        """A top-level function counts as used when its own module names it, another module imports it or reads it
        off the module (`from ..logic import atp` then `atp.allocate`), or a dispatch table names it as a string."""
        mods = {module_name(p): p for p in PY}
        trees = {m: ast.parse(p.read_text()) for m, p in mods.items()}
        defined = {(m, n.name): rel(mods[m]) for m, t in trees.items() for n in t.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.decorator_list}
        used, named = set(), set()
        for m, t in trees.items():
            pkg = m.split(".") if mods[m].name == "__init__.py" else m.split(".")[:-1]
            alias = {}
            for n in ast.walk(t):
                if isinstance(n, ast.Import):
                    for a in n.names:
                        alias[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
                elif isinstance(n, ast.ImportFrom):
                    base = ".".join(pkg[:len(pkg) - n.level + 1] + ([n.module] if n.module else [])) if n.level else n.module
                    for a in n.names:
                        if f"{base}.{a.name}" in mods:
                            alias[a.asname or a.name] = f"{base}.{a.name}"
                        used.add((base, a.name))
            for n in ast.walk(t):
                if isinstance(n, ast.Name):
                    used.add((m, n.id))
                elif isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in alias:
                    used.add((alias[n.value.id], n.attr))
                elif isinstance(n, ast.Constant) and isinstance(n.value, str):
                    named.add(n.value)
        dead = sorted(f"{path}: {name}" for (m, name), path in defined.items()
                      if (m, name) not in used and name not in named and not name.startswith("test") and name != "main")
        self.assertEqual(dead, [])

    def test_every_javascript_function_is_used(self):
        srcs = {p: p.read_text() for p in JS}
        dead = []
        for p, src in srcs.items():
            for name, exported, _ in js_functions(src):
                if name in PAGE_CONTRACT:
                    continue
                here = len(re.findall(r"(?<![\w$.])%s\b" % re.escape(name), src)) > 1
                elsewhere = exported and any(re.search(r"\b%s\b" % re.escape(name), s) for q, s in srcs.items() if q != p)
                if not (here or elsewhere):
                    dead.append(f"{rel(p)}: {name}")
        self.assertEqual(dead, [])


class Duplicates(unittest.TestCase):
    MIN_SIZE = 40        # one-line bodies shorter than this (a bare return) are not worth a shared home

    def test_no_python_function_body_is_copied_between_files(self):
        seen = defaultdict(set)
        for p in PY:
            for n in ast.walk(ast.parse(p.read_text())):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    body = ast.Module(body=n.body[1:] if ast.get_docstring(n) else n.body, type_ignores=[])
                    if len(ast.unparse(body)) >= self.MIN_SIZE:
                        seen[ast.dump(body)].add(f"{rel(p)}: {n.name}")
        copies = [sorted(v) for v in seen.values() if len({x.split(":")[0] for x in v}) > 1]
        self.assertEqual(copies, [])

    def test_no_javascript_function_body_is_copied_between_files(self):
        seen = defaultdict(set)
        for p in JS:
            for name, _, code in js_functions(p.read_text()):
                code = re.sub(r"\s+", " ", re.sub(r"//[^\n]*", "", code)).strip()
                if name not in PAGE_CONTRACT and len(code) >= self.MIN_SIZE:
                    seen[code].add(f"{rel(p)}: {name}")
        copies = [sorted(v) for v in seen.values() if len({x.split(":")[0] for x in v}) > 1]
        self.assertEqual(copies, [])


if __name__ == "__main__":
    unittest.main()
