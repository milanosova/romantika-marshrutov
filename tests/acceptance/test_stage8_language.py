"""Stage 8 acceptance: the language of the code (CLAUDE.md rule 7).

OWNED BY DIMA (CLAUDE.md rule 5). The first version of the bot was written with Russian
identifiers and file names; this must not come back. Two checks:

1. No Cyrillic in identifiers (functions, classes, variables, arguments, attributes, imports,
   keyword arguments) or in file and directory names under `romantika/`, `scripts/`, `tests/`.
2. A ratchet on Russian string literals outside `romantika/texts/`: the count per package may
   go down, never up. Product texts belong to `romantika/texts/ru.py` and the templates.
   When a package legitimately needs more Russian literals (a demo text in `ops`, a message
   the schema validator returns), Dima lowers or raises the baseline here, in the same commit.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CYRILLIC = re.compile(r"[А-Яа-яЁё]")

# Measured 2026-09-16 on commit 8f4646e + the harness. Lower is always fine.
BASELINE = {
    "bot": 127,
    "db": 6,
    "domain": 2,
    "migration": 99,
    "ops": 78,
    "pdf": 34,
    "services": 49,
    "web": 53,
    "worker": 16,
}


def python_files(*roots: str) -> list[Path]:
    return [p for root in roots for p in sorted((REPO / root).rglob("*.py")) if ".venv" not in p.parts]


def identifiers(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.append(node.name)
        elif isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.arg):
            names.append(node.arg)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.alias):
            names.append(node.asname or node.name)
        elif isinstance(node, ast.keyword) and node.arg:
            names.append(node.arg)
    return names


def test_no_cyrillic_identifiers() -> None:
    offenders = []
    for path in python_files("romantika", "scripts", "tests"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders += [(path.relative_to(REPO).as_posix(), name) for name in identifiers(tree) if CYRILLIC.search(name)]
    assert offenders == [], f"Cyrillic identifiers: {offenders[:10]}"


def test_no_cyrillic_file_names() -> None:
    offenders = [
        p.relative_to(REPO).as_posix()
        for root in ("romantika", "scripts", "tests", "docs", ".claude", "brain/_templates")
        for p in (REPO / root).rglob("*")
        if CYRILLIC.search(p.name) and ".venv" not in p.parts
    ]
    assert offenders == [], f"Cyrillic file names: {offenders[:10]}"


def test_russian_literals_outside_texts_do_not_grow() -> None:
    counts: Counter[str] = Counter()
    for path in python_files("romantika"):
        rel = path.relative_to(REPO / "romantika")
        package = rel.parts[0] if len(rel.parts) > 1 else "(root)"
        if package == "texts":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        counts[package] += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and CYRILLIC.search(node.value)
        )
    grown = {pkg: (n, BASELINE.get(pkg, 0)) for pkg, n in counts.items() if n > BASELINE.get(pkg, 0)}
    assert grown == {}, (
        f"Russian string literals grew outside romantika/texts/: {grown} (package: (now, baseline)). "
        "Product texts go to romantika/texts/ru.py; only Dima changes the baseline."
    )
