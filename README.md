# Романтика маршрутов · v2

Telegram-бот клуба [@romantika_marshrutov](https://t.me/romantika_marshrutov) и его Mini Apps:
задания недели, паспорт сезона со штампами и заморозками, словарь, факты, журнал сезона
(в боте, в приложении и в PDF), админка для Милы. Один бот, несколько веб-страниц, Postgres,
фото участников на нашем сервере с проверяемыми бэкапами.

Продуктовые правила: [`docs/DOMAIN.md`](docs/DOMAIN.md). Техконтракт: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Эксплуатация: [`docs/RUNBOOK.md`](docs/RUNBOOK.md). Руководство владельца: [`docs/GUIDE-RU.md`](docs/GUIDE-RU.md).
Правила для Claude Code: [`CLAUDE.md`](CLAUDE.md). Старый код лежит в `legacy/` только для справки.

## Что внутри

| Часть | Где | Запуск |
|---|---|---|
| Бот (aiogram 3) | `romantika/bot` | `python -m romantika.bot` |
| Веб: API, Mini Apps «Журнал» и «Админка», публичная страница сезона, календарь цолькин | `romantika/web` | `python -m romantika.web` (порт 8010) |
| Воркер: очередь заданий, PDF-журнал, напоминания, контроль бэкапов | `romantika/worker` | `python -m romantika.worker` |
| Сервисы и правила | `romantika/services`, `romantika/domain` | — |
| База (SQLAlchemy 2 + Alembic) | `romantika/db` | `make migrate` |
| Бэкапы и восстановление | `romantika/ops`, `scripts/` | `scripts/backup.sh`, `scripts/restore-verify.sh` |
| Импорт из старого бота | `romantika/migration` | `python -m romantika.migration.legacy_import --sqlite …` |
| Контейнеры | `docker/` | `docker compose -f docker/compose.yml --project-directory . up -d` |
| Локальный стенд без Telegram (заглушка Bot API, подписанные ссылки) | `romantika/ops/fake_telegram.py`, `scripts/dev-stack.sh` | `scripts/dev-stack.sh up` |

## Локальная разработка

Нужны: Python 3.12 через [uv](https://docs.astral.sh/uv/), Docker (Postgres для тестов поднимается
сам через testcontainers), для PDF на macOS — `brew install pango`, для тестов бэкапа —
`brew install libpq` (pg_dump).

```bash
uv sync                 # зависимости
cp .env.example .env    # и заполнить BOT_TOKEN, ADMIN_IDS, MEDIA_DIR
make check              # ruff + format + mypy + pytest (полная проверка, ~15 с)
make run-web            # http://127.0.0.1:8010
make run-bot            # long polling с токеном из .env
make run-worker
```

Полный стенд без Telegram: `scripts/dev-stack.sh up` поднимает Postgres, заглушку Bot API, веб,
бота и воркер; `scripts/dev-stack.sh link 1001 Алиса` печатает ссылку на Mini App под этим
пользователем. Подробнее — в RUNBOOK, раздел «Local stand».

Тесты: `tests/acceptance/` — приёмочные контракты по этапам (не менять без обсуждения),
`tests/unit/`, `tests/integration/`. CI (`.github/workflows/ci.yml`) гоняет то же, что `make check`.

## Прод

Стек живёт на VPS в `/opt/stacks/romantika`, деплой одной командой `scripts/deploy.sh`
(rsync + сборка образа на сервере + миграции + рестарт + smoke). Подробности, cut-over со старого
бота, бэкапы и восстановление — в [`docs/RUNBOOK.md`](docs/RUNBOOK.md).
