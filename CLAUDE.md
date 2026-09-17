# Romantika Marshrutov — rules for Claude Code

This repository is the Telegram bot + Mini Apps of the club «Романтика маршрутов». The owner
(Mila) is not a developer and works with Claude Code alone: nobody else reviews the code before
it reaches real people, so Claude carries the whole engineering discipline itself. Talk to Mila
in Russian, in product terms; write code and developer docs in English.

Read first: `docs/ARCHITECTURE.md` (binding technical contract), `docs/DOMAIN.md` (product
rules, Russian), `docs/RUNBOOK.md` (production: access, deploy, backups, rollback),
`brain/README.md` (the project memory: tasks, bugs, ideas, production snapshots). Legacy code
in `legacy/` is reference only.

## Hard rules

1. Never delete participant data or media. No `DELETE` on `reports`, `media`, `stamps`,
   `freezes`, `achievements`, `words`, `facts`, `wishes`, `letters`; use `deleted_at`/`hidden_at`.
   Never remove files under `MEDIA_DIR`, never touch the Docker volumes `pgdata` and `media`,
   never `docker compose down -v`. Production data never leaves the VPS: the local stand runs
   on generated demo data (`romantika/ops/demo_data.py`), never on a copy of production.
2. Schema changes only through Alembic migrations in `romantika/db/migrations/versions/`:
   additive and reversible, never editing an applied migration. A migration that rewrites
   data is a heavy decision (rule 11) and needs a fresh backup first.
3. Layers: business rules live in `romantika/domain` (pure) and `romantika/services`; bot
   handlers and web routes only translate transport ↔ services; the web never talks to
   Telegram (it queues jobs for the worker). Product texts live in `romantika/texts/ru.py` and
   the templates, never inline in handlers or JS. A change that needs a new pattern updates
   `docs/ARCHITECTURE.md` in the same commit.
4. A product rule changes only together with `docs/DOMAIN.md`; behaviour people can see
   changes only together with `docs/CHANGELOG.md` and, when it touches Mila's routine,
   `docs/GUIDE-RU.md`.
5. `tests/acceptance/` is owned by Dima. Claude does not edit it. If an acceptance test seems
   wrong, say so in the report and in `brain/skills-log.md` instead of changing it.
6. Secrets come from the environment only. Never commit `.env`, tokens, keys, dumps or media;
   never paste the bot token or a private key into a chat, a doc or a log. The test bot token
   lives in `.dev/dev-bot.env` (git-ignored), fetched from the VPS per `docs/SETUP-RU.md`.
7. Language. Code, identifiers, comments, commits, developer docs: English. Everything shown
   to people: Russian, in Mila's voice (first person, warm, no jargon; reread
   `romantika/texts/ru.py` for the tone). Cyrillic in an identifier or a file name is a bug;
   `tests/acceptance/test_stage8_language.py` fails on it and on new Russian strings outside
   `romantika/texts/`. Answers to Mila: Russian.
8. Before saying «готово»: `make check` is green (ruff, format, mypy, pytest) with a real
   denominator («N passed», N growing when behaviour was added), and the checks of the route
   below have run. Red is red: fix the code, never skip or weaken a test.
9. No new runtime dependencies without a sentence in the commit message explaining why.
10. Production is reached only through `scripts/deploy.sh` and the RUNBOOK procedures: never
    by editing files on the VPS by hand, never by running SQL against the production database
    unless the RUNBOOK names the query. Restore only per RUNBOOK «Restore», after a fresh backup.
11. Heavy technical decisions are flagged, not blocked. When a change touches
    `romantika/db/` (a migration that rewrites data), `docker/`, `scripts/deploy.sh`,
    `scripts/backup.sh`, `romantika/ops/backup.py`, access or secrets, a new external service or
    dependency, or anything Claude cannot verify on the stand — say to Mila, in these words:
    «Это сложное техническое решение — лучше уточнить у Димы», explain the risk in one
    paragraph, and continue only if she says so.
12. Agents never write production code. Claude writes the code itself and spawns agents with
    fresh context as critics, testers and editors (`.claude/agents/`). A finding without proof
    (file:line and a quote, request and response, log lines, a screenshot) is not a finding.

## First: name the route

Every request from Mila starts with the route, said aloud in the first sentence of the answer
(«это контент — сделаем в админке», «это микро-правка», «это фича — готовлю план», «это
авария»). When in doubt take the heavier route; Mila can override.

Mila's word is needed three times for a feature — «ок» on the plan, «сливаем» after the stand
check, «выкатываем» before production — and once for a micro-change (her «ок» covers both `dev`
and production). Nothing else waits for her.

| Route | What it is | What runs |
|---|---|---|
| **Контент** | texts of weeks, words, facts, achievements, wishes, reminders toggle | nothing in the code: Admin Mini App or bot commands (`docs/GUIDE-RU.md`) |
| **Микро-правка** | one label, one text of the bot, a colour; no behaviour change | branch from `dev` → change + `make check` → `/proverka` light → merge into `dev` → `/relize` |
| **Фича или заметный баг** | anything people will notice | `/zadacha` (plan page, task card, branch) → build on the stand → `/proverka` → Mila's «ок» → `dev` → `/relize` (full checks, live run, deploy) |
| **Авария** | production is broken now | `/avaria`: backup → diagnose from logs → rollback or minimal fix on `master` → deploy → report after |

## Branches

`master` is production. `dev` is what has been verified on the stand and is waiting for a
release. `feature/NN-slug` branches from `dev` (`NN` is the task number in `brain/tasks/`). A
hotfix branches from `master` and is merged into both `master` and `dev`. Nothing is committed
directly to `master` except an avaria hotfix. Merging into `master` happens only inside
`/relize` (after Mila said «выкатываем») or inside `/avaria` (a hotfix is merged and deployed
without waiting for her word; she is told right after).

## Skills are the procedures

| Skill | Use |
|---|---|
| `/zadacha` | take a request, name the route, ask at most three questions, write the plan page, create the task card and the branch |
| `/stend` | start, stop, reset the local stand; `work` mode (fake Telegram + demo data) or `live` mode (test bot in real Telegram) |
| `/proverka` | first gate before `dev`: `make check`, the feature on the stand, log reading, two critics |
| `/relize` | second gate before `master`: full critics, regression list, live run, release window, deploy, watch, report. Only when Mila herself says «выкатываем», «релиз», «на прод»; never as a step of another skill |
| `/otchet` | write the plan or the report page for Mila and have `editor-report` review it before she sees it |
| `/avaria` | production is down: the emergency route |
| `/status` | where we are: branch, open tasks, next step for Mila |
| `/prod` | snapshot of production: activity, errors, backups, disk; written to `brain/ops/` |

The skills describe the procedures in full; this file only names them.

## Memory: `brain/`

`brain/tasks/NN-slug/` holds `status.md` (state, branch, acceptance checklist, journal),
`plan.html` and `report.html`. `brain/backlog.md` is generated by `scripts/brain_index.py`
(never edited by hand). `brain/bugs/`, `brain/ideas/`, `brain/ops/` and `brain/skills-log.md`
are described in `brain/README.md`. Every change of a task's state updates its `status.md`
and regenerates the backlog; every session starts with `/status`.

## Everyday commands

```
uv sync                                 # install
make check                              # ruff + format check + mypy + pytest (Docker for Postgres)
scripts/dev-stack.sh up                 # local stand, work mode: Postgres, fake Bot API, web, bot, worker, demo data
scripts/dev-stack.sh up --live          # local stand, live mode: the test bot in real Telegram (token from .dev/dev-bot.env)
scripts/dev-stack.sh link 1001 Алиса    # a signed Mini App link for a stand participant
scripts/dev-stack.sh logs               # tail the stand logs (.dev/logs/*.log)
scripts/shots.sh                        # screenshots of the Mini App screens into .dev/shots/
python3 scripts/brain_index.py          # regenerate brain/backlog.md
scripts/deploy.sh                       # production; SSH alias romantika-vps (RUNBOOK «Access»); only inside /relize or /avaria
```
