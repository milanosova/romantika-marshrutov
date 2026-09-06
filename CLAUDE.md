# Romantika Marshrutov — rules for Claude Code

This repository is the Telegram bot + Mini Apps of the club «Романтика маршрутов». The owner
(Mila) is not a developer and works with Claude Code directly, so Claude carries the whole
engineering discipline itself: nobody else reviews the code before it reaches real people.
Every change must keep the product safe by construction.

Read first: `docs/ARCHITECTURE.md` (binding technical contract), `docs/DOMAIN.md` (product
rules, Russian), `docs/RUNBOOK.md` (production: access, deploy, backups, rollback). Legacy code
in `legacy/` is reference only.

## Hard rules

1. Never delete participant data or media. No `DELETE` on `reports`, `media`, `stamps`,
   `freezes`, `achievements`, `words`, `facts`, `wishes`, `letters`; use `deleted_at`/`hidden_at`.
   Never remove files under `MEDIA_DIR`, never touch the Docker volumes `pgdata` and `media`,
   never `docker compose down -v`.
2. Schema changes only through Alembic migrations in `romantika/db/migrations/versions/`:
   additive and reversible, never editing an applied migration. A migration that rewrites
   data goes through the data review gate (below) and a fresh backup first.
3. Layers: business rules live in `romantika/domain` (pure) and `romantika/services`; bot
   handlers and web routes only translate transport ↔ services; the web never talks to
   Telegram (it queues jobs for the worker). Product texts live in `romantika/texts/ru.py` and
   the templates, never inline in handlers or JS. Read `docs/ARCHITECTURE.md` before adding a
   module; a change that needs a new pattern updates the document in the same commit.
4. A product rule changes only together with `docs/DOMAIN.md`; behaviour people can see
   changes only together with `docs/CHANGELOG.md` and, when it touches Mila's routine,
   `docs/GUIDE-RU.md`.
5. `tests/acceptance/` belongs to the reviewers: the agent that wrote the code does not edit
   them. If an acceptance test seems wrong, say so in the report instead of changing it.
6. Secrets come from the environment only. Never commit `.env`, tokens, keys, dumps or media;
   never paste the bot token or a private key into a chat, a doc or a log.
7. Code, comments, commits, developer docs: English. Everything shown to people: Russian, in
   Mila's voice (first person, warm, no jargon); reread `romantika/texts/ru.py` for the tone.
8. Before saying «готово»: `make check` is green (ruff, format, mypy, pytest) with a real
   denominator («N passed», N growing when behaviour was added), and the critique loop below
   has run for anything people will see or that touches data.
9. No new runtime dependencies without a sentence in the commit message explaining why.
10. Production is reached only through `scripts/deploy.sh` and the RUNBOOK procedures: never
    by editing files on the VPS by hand, never by running SQL against the production database
    unless the RUNBOOK names the query. Restore only per RUNBOOK «Restore», after a fresh backup.

## How a change is made

Claude is the orchestrator and the implementer: it writes the code itself and spawns a few
agents with fresh context as testers, critics and reviewers. Agents never write production
code. A finding without a quote (file:line, request and response, screenshot) is not a finding.

1. **Understand.** Restate the request in product terms; find the rule in `docs/DOMAIN.md` and
   the contract in `docs/ARCHITECTURE.md`. If they disagree with the request, say so before
   coding: the documents are the source of truth until Mila changes them.
2. **Build on a branch.** `git switch -c <topic>` from `master`. Domain → services →
   transport; tests next to the existing ones (`tests/unit`, `tests/integration`); texts in
   `ru.py`. Keep the diff to the request, no drive-by refactors.
3. **Check.** `make check`. Red is red: fix the code, never skip or weaken a test.
4. **Critique loop** (mandatory when the change is visible to participants or Mila, or touches
   data): start the local stand (`scripts/dev-stack.sh up`, no Telegram needed) and spawn one
   or two critics with the Agent tool: `forge-reviewer-ui` for the product surface (it uses
   the stand as a participant and as Mila, screenshots go to `.dev/`), `forge-reviewer-code`
   for correctness over the diff. They report, Claude fixes, repeat. Stop after three rounds
   and tell Mila what is left. Every wording they flag is a real finding: the texts must read
   as one voice, and the app must not look thrown together.
5. **Data review gate** (mandatory when the diff touches `romantika/db/`, `romantika/worker/`,
   `romantika/ops/`, `romantika/pdf/`, `romantika/services/{media,reports,stamps}.py`,
   `scripts/backup.sh`, `scripts/restore-verify.sh` or `docker/`): run `/release-check`. Its
   `forge-reviewer-data` checks invariants, denominators and «false green» in the pipelines
   (backups, restore check, PDF, journals, notifications). Deploy only with zero blocking
   findings.
6. **Release.** `docs/RUNBOOK.md` «Release checklist»: `DRY=1 scripts/deploy.sh`, then
   `scripts/deploy.sh`; `healthz` must say `"status":"ok"`; open the bot and the Mini App
   once; watch `rc logs -f bot worker web` for two minutes. Then merge the branch into
   `master` and push.
7. **Report to Mila in Russian**, in product terms: what changed, how it was verified (tests,
   which critics ran, what they found), what to try, what was deliberately not touched.

## Everyday commands

```
uv sync                                 # install
make check                              # ruff + format check + mypy + pytest (Docker for Postgres)
scripts/dev-stack.sh up                 # local stand: Postgres, fake Bot API, web, bot, worker
scripts/dev-stack.sh link 1001 Алиса    # a signed Mini App link for a stand participant
make run-web                            # local web only (dev auth bypass)
make migrate                            # alembic upgrade head
scripts/deploy.sh                       # production; SSH alias romantika-vps (RUNBOOK «Access»)
```
