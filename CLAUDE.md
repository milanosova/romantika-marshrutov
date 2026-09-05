# Romantika Marshrutov — rules for Claude Code

This repository is the Telegram bot + Mini Apps of the club «Романтика маршрутов». The
product owner is not a developer: every change must keep the product safe by construction.

Read first: `docs/ARCHITECTURE.md` (binding technical contract), `docs/DOMAIN.md` (product
rules, Russian). Legacy code in `legacy/` is reference only.

## Hard rules

1. Never delete participant data or media. No `DELETE` on `reports`, `media`, `stamps`,
   `freezes`, `achievements`, `words`, `facts`, `wishes`; use `deleted_at`/`hidden_at`.
   Never remove files under `MEDIA_DIR`.
2. Schema changes only through Alembic migrations in `romantika/db/migrations/versions/`.
   Never edit an already applied migration; add a new one. Every migration is reversible.
3. `tests/acceptance/` is owned by the reviewers/orchestrator. Implementers do not modify it.
   If an acceptance test seems wrong, say so in the report instead of changing it.
4. Business rules live in `romantika/domain` (pure) and `romantika/services`; handlers and
   routes only translate transport ↔ services. Product texts live in `romantika/texts`
   and templates.
5. Secrets come from the environment only. Never commit `.env`, tokens, dumps or media.
6. Code, comments, commits, docs for developers: English. Texts shown to people: Russian.
7. Before saying "done": `make check` is green (ruff, ruff format, mypy, pytest) and new
   behaviour has tests. Denominators matter: "N passed" with N > 0.
8. No new runtime dependencies without a line in the PR description explaining why.
9. Deployment and backups are described in `docs/RUNBOOK.md`; production actions
   (deploy, restore) are run by a person, never automatically from a Claude session.

## Everyday commands

```
uv sync                 # install
make check              # ruff + format check + mypy + pytest (needs Docker for Postgres)
make run-web            # local web on http://127.0.0.1:8010 (dev auth bypass)
make run-bot            # local bot polling (needs BOT_TOKEN in .env)
make migrate            # alembic upgrade head
```

## Change workflow (for the owner working with Claude)

1. Describe the change in product terms; Claude finds the rule in `docs/DOMAIN.md` and
   updates the document together with the code and tests.
2. Claude works on a branch, runs `make check`, and writes a short summary: what changed,
   which tests prove it, what was not touched.
3. Release: `docs/RUNBOOK.md` → "Release checklist". Reviews run through the project's
   `/release-check` skill (multi-agent verification) before deploy.
