---
name: release-check
description: Pre-release verification of the current branch — deterministic checks (make check) by an independent verifier, then code, security and data reviewers over the diff, with findings verified and summarised. Use before every deploy, or when the owner asks «проверь релиз», «/release-check».
---

# Release check

You are the orchestrator. Do not review the code yourself; run the workflow below and relay the
result honestly. Nothing here deploys anything.

1. Make sure the working tree is committed (`git status`), note the base: the merge-base with
   `main` (or `master`), or the previous tag. The diff under review is `git diff <base>..HEAD`.
2. Run `Workflow({scriptPath: ".claude/workflows/release-check.js", args: {base: "<base>"}})`.
   It runs `forge-verifier` (make check, with denominators), then `forge-reviewer-code`,
   `forge-reviewer-security` and `forge-reviewer-data` in parallel over the diff, and returns
   `{green, findings}`. Add the `ui` lens (`forge-reviewer-ui`) when the diff touches
   `romantika/web/templates`, `romantika/web/static` or bot texts.
3. Report to the owner in Russian: was `make check` green (tests passed / total), the
   blocking findings (critical/important) with file:line and a one-line scenario each, the
   nits as a short list. A finding without a code quote is not a finding.
4. If there are blocking findings: fix them (or ask the owner), commit, and run the check again.
   Deploy only after a green run with zero blocking findings — see docs/RUNBOOK.md «Release checklist».

Rules that reviewers must respect (from CLAUDE.md): never delete participant data or media,
migrations only additive and reversible, acceptance tests are owned by the reviewers.
