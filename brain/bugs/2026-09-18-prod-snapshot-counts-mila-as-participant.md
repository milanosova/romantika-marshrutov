---
title: Снимок прода считает Милу участницей — `users.is_admin` на проде не выставлен
status: open
severity: низкая
found: 2026-09-18
---

# Снимок прода считает Милу участницей

Найден 2026-09-18 критиком данных при `/relize` v2.5.0. Старое.

**Симптом.** `scripts/prod-snapshot.sh` («участниц всего: 2») и запрос в RUNBOOK
`… where blocked_at is null and is_admin = false` считают Милу участницей: колонка
`users.is_admin` пишется только демо-данными стенда (`romantika/ops/demo_data.py`), на проде
Мила — админ через `ADMIN_IDS` (`web/deps.py`: `user.is_admin or settings.is_admin(id)`).

**Что сделать.** Либо выставлять `users.is_admin` при старте по `ADMIN_IDS` (сервис + строка в
ARCHITECTURE), либо в снимке читать `ADMIN_IDS` из `.env` на VPS и исключать эти id. Новый
запрос про факты Милы в RUNBOOK (v2.5.0) уже переписан на `<ADMIN_IDS>`.
