"""Stage 7 acceptance: documentation and the Claude-driven change process.

READ-ONLY for implementers. Static checks that the repository is self-describing enough for a
non-technical owner working through Claude Code (CLAUDE.md, RUNBOOK, GUIDE-RU, in-repo review
agents and the release-check skill, CI).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    p = REPO / path
    assert p.exists(), f"missing {path}"
    return p.read_text(encoding="utf-8")


def test_claude_md_has_rules_and_commands() -> None:
    text = read("CLAUDE.md")
    for needle in ("docs/ARCHITECTURE.md", "docs/DOMAIN.md", "make check", "tests/acceptance", "Never delete", "release-check", "docs/RUNBOOK.md"):
        assert needle in text, f"CLAUDE.md lacks {needle!r}"


def test_runbook_covers_deploy_backup_restore_cutover() -> None:
    text = read("docs/RUNBOOK.md")
    for heading in ("Deploy", "Backup", "Restore", "Cut-over", "Release checklist", "Rollback", "Logs"):
        assert re.search(rf"^#+\s*.*{heading}", text, flags=re.MULTILINE | re.IGNORECASE), f"RUNBOOK lacks section {heading!r}"
    assert "/opt/stacks/romantika" in text and "restore-verify" in text and "mac-pull-backups" in text


def test_owner_guide_in_russian() -> None:
    text = read("docs/GUIDE-RU.md")
    for needle in ("админ", "Mini App", "бэкап", "Claude", "PDF", "недел"):
        assert needle.lower() in text.lower(), f"GUIDE-RU lacks {needle!r}"
    assert len(text) > 3000


def test_readme_describes_v2() -> None:
    text = read("README.md")
    assert "uv sync" in text and "make check" in text and "docker" in text.lower()


def test_in_repo_review_agents_and_release_skill() -> None:
    agents = {p.name for p in (REPO / ".claude" / "agents").glob("*.md")}
    for name in ("forge-implementer.md", "forge-verifier.md", "forge-reviewer-code.md", "forge-reviewer-security.md", "forge-reviewer-data.md", "forge-reviewer-ui.md"):
        assert name in agents, f"missing .claude/agents/{name}"
    skill = read(".claude/skills/release-check/SKILL.md")
    assert "make check" in skill and "forge-reviewer-code" in skill and "forge-verifier" in skill
    assert (REPO / ".claude" / "workflows" / "release-check.js").exists()
    workflow = read(".claude/workflows/release-check.js")
    assert "export const meta" in workflow and "forge-verifier" in workflow and "forge-reviewer-security" in workflow


def test_ci_runs_the_same_checks() -> None:
    ci = yaml.safe_load(read(".github/workflows/ci.yml"))
    jobs = ci["jobs"]
    steps = "\n".join(str(step.get("run", "")) for job in jobs.values() for step in job.get("steps", []))
    for needle in ("uv sync", "ruff check", "mypy", "pytest"):
        assert needle in steps, f"CI lacks {needle!r}"
    services = "\n".join(str(job.get("services", "")) for job in jobs.values())
    assert "postgres" in services


def test_env_example_lists_every_setting() -> None:
    example = read(".env.example")
    for key in ("BOT_TOKEN", "ADMIN_IDS", "DATABASE_URL", "MEDIA_DIR", "PUBLIC_BASE_URL", "ADMIN_CHAT_ID"):
        assert re.search(rf"^{key}=", example, flags=re.MULTILINE), f".env.example lacks {key}"
    assert not re.search(r"^BOT_TOKEN=\d{6,}:", example, flags=re.MULTILINE), "no real token in the example"


def test_domain_doc_is_referenced_by_tests() -> None:
    """Every DOMAIN.md section on rules has at least one test mentioning it (§2–§5)."""
    tests = "\n".join(p.read_text(encoding="utf-8") for p in (REPO / "tests").rglob("*.py"))
    for needle in ("freeze", "streak", "level", "tzolkin", "stamp"):
        assert needle in tests
