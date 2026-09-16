# Романтика маршрутов · v2

Telegram-бот клуба [@romantika_marshrutov](https://t.me/romantika_marshrutov) и его Mini Apps:
задания недели, паспорт сезона со штампами и заморозками, словарь, факты, журнал сезона
(в боте, в приложении и в PDF), админка для Милы. Один бот, несколько веб-страниц, Postgres,
фото участников на нашем сервере с проверяемыми бэкапами.

Продуктовые правила: [`docs/DOMAIN.md`](docs/DOMAIN.md). Техконтракт: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Эксплуатация: [`docs/RUNBOOK.md`](docs/RUNBOOK.md). Руководство владельца: [`docs/GUIDE-RU.md`](docs/GUIDE-RU.md).
Разовая настройка мака и GitHub: [`docs/SETUP-RU.md`](docs/SETUP-RU.md).
Правила для Claude Code: [`CLAUDE.md`](CLAUDE.md); процедуры — `.claude/skills/`; память проекта (задачи, планы,
отчёты, снимки прода) — [`brain/`](brain/README.md). Старый код лежит в `legacy/` только для справки.

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
| Локальный стенд: заглушка Bot API или тестовый бот, тридцать демо-участниц, подписанные ссылки | `romantika/ops/{fake_telegram,demo_data,chat_mockup}.py`, `scripts/dev-stack.sh`, `scripts/shots.sh` | `scripts/dev-stack.sh up [--live]` |

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

Стенд: `scripts/dev-stack.sh up` поднимает Postgres, заглушку Bot API, веб, бота, воркер и
заливает тридцать придуманных участниц; `up --live` — то же с тестовым ботом в настоящем
Telegram; `scripts/dev-stack.sh link 1001 Алиса` печатает ссылку на Mini App под этим
пользователем; `scripts/shots.sh` снимает экраны, `python -m romantika.ops.chat_mockup` собирает
макет переписки с ботом. Подробнее — в RUNBOOK, раздел «Local stand».

Тесты: `tests/acceptance/` — приёмочные контракты по этапам, их правит только Дима (стадия 7 —
харнес, стадия 8 — язык кода), `tests/unit/`, `tests/integration/`. CI (`.github/workflows/ci.yml`) гоняет то же, что `make check`.

## Прод

Стек живёт на VPS в `/opt/stacks/romantika`, деплой одной командой `scripts/deploy.sh`
(rsync + сборка образа на сервере + миграции + рестарт + smoke). Подробности, cut-over со старого
бота, бэкапы и восстановление — в [`docs/RUNBOOK.md`](docs/RUNBOOK.md).
