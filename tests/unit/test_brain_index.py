"""`scripts/brain_index.py`: the backlog is generated from the task cards and validates them."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "brain_index.py"

CARD = """---
id: {id}
slug: {slug}
title: {title}
route: {route}
status: {status}
branch: feature/{id}-{slug}
created: 2026-09-16
updated: 2026-09-16
plan: plan.html
report:
---

# {id} · {title}

## Приёмка

- [x] first
- [ ] second

## Журнал

- 2026-09-16 — created.
"""


@pytest.fixture
def brain_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("brain_index", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "BRAIN", tmp_path)
    (tmp_path / "tasks").mkdir()
    (tmp_path / "bugs").mkdir()
    return module


def write_card(root: Path, **fields: str) -> Path:
    folder = root / "tasks" / f"{fields['id']}-{fields['slug']}"
    folder.mkdir(parents=True)
    card = folder / "status.md"
    card.write_text(CARD.format(**fields), encoding="utf-8")
    return card


def test_render_groups_tasks_by_state_and_counts_acceptance(brain_index, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    write_card(tmp_path, id="01", slug="first", title="Первая", route="feature", status="in_progress")
    write_card(tmp_path, id="02", slug="second", title="Вторая", route="micro", status="on_prod")
    (tmp_path / "bugs" / "2026-09-16-photo.md").write_text(
        "---\ntitle: Фото не грузится\nstatus: open\nseverity: critical\nfound: 2026-09-16\n---\n", encoding="utf-8"
    )
    text, problems = brain_index.render()
    assert problems == []
    assert "## В работе" in text and "## На проде" in text
    assert "| 01 | [Первая](tasks/01-first/status.md) | фича | `feature/01-first` | 1/2 |" in text
    assert "## Открытые баги" in text and "Фото не грузится" in text


def test_render_reports_invalid_cards(brain_index, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    card = write_card(tmp_path, id="03", slug="bad", title="Кривая", route="feature", status="done")
    (tmp_path / "tasks" / "04-nofm").mkdir()
    (tmp_path / "tasks" / "04-nofm" / "status.md").write_text("# no front matter\n", encoding="utf-8")
    _text, problems = brain_index.render()
    assert any("unknown status `done`" in p for p in problems)
    assert any("no front matter" in p for p in problems)
    assert str(card) in problems[0]


def test_main_writes_backlog_and_fails_on_problems(  # type: ignore[no-untyped-def]
    brain_index, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_card(tmp_path, id="01", slug="ok", title="Ок", route="feature", status="planned")
    assert brain_index.main() == 0
    assert (tmp_path / "backlog.md").exists()
    write_card(tmp_path, id="02", slug="bad", title="Не ок", route="nope", status="planned")
    assert brain_index.main() == 1
    assert "unknown route" in capsys.readouterr().err
