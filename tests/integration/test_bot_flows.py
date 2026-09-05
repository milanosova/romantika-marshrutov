"""Integration tests of the bot layer: every dialog state, button, panel action and command.

The acceptance suite (`tests/acceptance/test_stage3_bot.py`) checks that the contract holds; this
suite goes through the product rules of DOMAIN §2, §3, §7 and §8 screen by screen and asserts on
what was actually sent and what ended up in the database, not merely that nothing blew up.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from aiogram.methods import AnswerCallbackQuery, CopyMessage, SendMessage
from aiogram.types import Chat, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from romantika.db import models
from romantika.services import content, people
from romantika.services.people import DIALOG_TTL, TelegramUser
from tests.integration.bot_harness import (
    ADMIN_ID,
    ALICE,
    BOB,
    WEEK2,
    FakeTelegram,
    Harness,
    build_harness,
    moscow,
)


async def dialog_row(db_session: AsyncSession, user_id: int) -> models.DialogState | None:
    return await db_session.get(models.DialogState, user_id)


async def count(db_session: AsyncSession, model: type) -> int:
    return int((await db_session.execute(select(func.count()).select_from(model))).scalar_one())


# =====================================================================================
# 1. Dialog states: word / letter / fact / edit / wish, their reset and their TTL
# =====================================================================================


async def test_word_dialog_saves_word_clears_state_and_grants_freeze(
    harness: Harness, db_session: AsyncSession
) -> None:
    await harness.callback(ALICE, "addword")
    assert (await dialog_row(db_session, ALICE)).state == "word"

    await harness.text(ALICE, "sobremesa — время за столом уже после еды")

    word = (await db_session.execute(select(models.Word).where(models.Word.user_id == ALICE))).scalar_one()
    assert word.word == "sobremesa" and "время за столом" in word.meaning
    assert await dialog_row(db_session, ALICE) is None, "the state must be cleared after the answer"
    assert "+1 заморозка" in harness.session.last_text(ALICE), "the first own word earns a freeze"
    freezes = (await db_session.execute(select(models.Freeze.reason).where(models.Freeze.user_id == ALICE))).scalars()
    assert list(freezes) == ["word"]
    assert "Новое слово от" in harness.session.all_text(ADMIN_ID)
    assert await count(db_session, models.Report) == 0, "an answer to the bot is not a report"


async def test_letter_dialog_reaches_mila_and_is_linked_for_the_reply(
    harness: Harness, db_session: AsyncSession
) -> None:
    await harness.text(ALICE, "⋯ Ещё")
    await harness.callback(ALICE, "more:write")
    assert (await dialog_row(db_session, ALICE)).state == "letter"

    await harness.text(ALICE, "Мила, я на неделю уезжаю")
    assert "Передала" in harness.session.last_text(ALICE)
    assert await dialog_row(db_session, ALICE) is None
    assert await count(db_session, models.Report) == 0, "a letter is not a report"

    header = harness.session.messages(ADMIN_ID)[-1]
    assert "Мила, я на неделю уезжаю" in header.text
    link = await db_session.get(models.AdminLink, (ADMIN_ID, harness.session.message_id_of(header)))
    assert link is not None and link.user_id == ALICE and link.report_id is None

    await harness.text(ADMIN_ID, "Хорошей дороги!", reply_to=harness.admin_message(header))
    assert "Мила ответила" in harness.session.last_text(ALICE)
    assert "Хорошей дороги!" in harness.session.last_text(ALICE)


async def test_fact_dialog_from_participant_keeps_the_author(harness: Harness, db_session: AsyncSession) -> None:
    await harness.callback(ALICE, "addfact")
    await harness.text(ALICE, "Ацтеки называли себя мешика")
    fact = (await db_session.execute(select(models.Fact))).scalar_one()
    assert fact.text == "Ацтеки называли себя мешика" and fact.author_id == ALICE
    assert await dialog_row(db_session, ALICE) is None
    assert "Новый факт от" in harness.session.all_text(ADMIN_ID)


async def test_fact_dialog_from_admin_has_no_author(harness: Harness, db_session: AsyncSession) -> None:
    await harness.callback(ADMIN_ID, "addfact")
    await harness.text(ADMIN_ID, "Чиле-эн-ногада — блюдо цветов флага")
    fact = (await db_session.execute(select(models.Fact))).scalar_one()
    assert fact.author_id is None, "Mila writes facts without a name (DOMAIN §6)"
    assert "Фактов за сезон" in harness.session.last_text(ADMIN_ID)


async def test_admin_edit_dialog_applies_the_change_and_writes_the_audit_log(
    harness: Harness, db_session: AsyncSession
) -> None:
    await harness.callback(ADMIN_ID, "adm:edit")
    weeks_offered = [data for _, data in harness.session.buttons(ADMIN_ID)]
    assert "adm:week:1" in weeks_offered and "adm:week:2" in weeks_offered

    await harness.callback(ADMIN_ID, "adm:week:2")
    assert "adm:field:2:task_min" in [data for _, data in harness.session.buttons(ADMIN_ID)]

    await harness.callback(ADMIN_ID, "adm:field:2:task_min")
    assert (await dialog_row(db_session, ADMIN_ID)).payload == {"week_number": 2, "field": "task_min"}

    await harness.text(ADMIN_ID, "Новый минимум на пять минут")
    season = await content.active_season(db_session, today=date(2026, 9, 2))
    week = await content.week_by_number(db_session, season.id, 2)
    assert week.task_min == "Новый минимум на пять минут"
    assert await dialog_row(db_session, ADMIN_ID) is None
    audit = (await db_session.execute(select(models.AuditLog).where(models.AuditLog.entity == "week"))).scalars().all()
    assert audit and audit[-1].actor_id == ADMIN_ID
    assert "Новый минимум" in harness.session.all_text(ADMIN_ID)


async def test_admin_wish_dialog_writes_one_wish_per_season(harness: Harness, db_session: AsyncSession) -> None:
    await harness.text(ALICE, "привет")  # Alice must exist before Mila can pick her
    harness.session.reset()
    await harness.callback(ADMIN_ID, "adm:people:wish:0")
    assert (f"adm:wish:{ALICE}") in [data for _, data in harness.session.buttons(ADMIN_ID)]

    await harness.callback(ADMIN_ID, f"adm:wish:{ALICE}")
    assert (await dialog_row(db_session, ADMIN_ID)).payload == {"user_id": ALICE}

    await harness.text(ADMIN_ID, "Ты держала всю осень")
    wish = (await db_session.execute(select(models.Wish))).scalar_one()
    assert wish.user_id == ALICE and wish.text == "Ты держала всю осень"
    assert await dialog_row(db_session, ADMIN_ID) is None


async def test_command_in_the_middle_of_a_dialog_clears_it_and_is_not_a_report(
    harness: Harness, db_session: AsyncSession
) -> None:
    await harness.callback(ALICE, "more:write")
    await harness.text(ALICE, "/start")
    assert await dialog_row(db_session, ALICE) is None
    assert "Романтика маршрутов" in harness.session.last_text(ALICE)
    assert await count(db_session, models.Report) == 0
    assert not any("Сообщение от" in t for t in harness.session.sent_texts(ADMIN_ID))


async def test_keyboard_button_in_the_middle_of_a_dialog_clears_it(harness: Harness, db_session: AsyncSession) -> None:
    await harness.callback(ALICE, "addword")
    await harness.text(ALICE, "📘 Паспорт")
    assert "Паспорт сезона" in harness.session.last_text(ALICE)
    assert await dialog_row(db_session, ALICE) is None
    assert await count(db_session, models.Word) == 0, "the button must not be saved as a word"


async def test_dialog_state_survives_just_under_the_ttl(harness: Harness, db_session: AsyncSession) -> None:
    await harness.callback(ALICE, "more:write")
    harness.advance(DIALOG_TTL - timedelta(minutes=1))
    await harness.text(ALICE, "почти шесть часов спустя")
    assert "Передала" in harness.session.last_text(ALICE)
    assert await count(db_session, models.Report) == 0


async def test_dialog_state_expires_after_six_hours(harness: Harness, db_session: AsyncSession) -> None:
    await harness.callback(ALICE, "more:write")
    harness.advance(DIALOG_TTL + timedelta(minutes=1))
    await harness.text(ALICE, "Сделала минимум")
    assert await dialog_row(db_session, ALICE) is None, "the stale state must be dropped"
    report = (await db_session.execute(select(models.Report))).scalar_one()
    assert report.kind == "text", "after the TTL the message is an ordinary report again"
    assert "минимум" in harness.session.last_text(ALICE).lower()


# =====================================================================================
# 2. Inline buttons of a participant
# =====================================================================================


@pytest.mark.parametrize(("choice", "word"), [("take", "берусь"), ("try", "попробую"), ("skip", "мимо")])
async def test_intent_buttons_store_the_choice_and_tell_mila(
    harness: Harness, db_session: AsyncSession, choice: str, word: str
) -> None:
    await harness.text(ALICE, "📋 Задание")
    await harness.callback(ALICE, f"intent:1:{choice}")
    row = (await db_session.execute(select(models.WeekIntent).where(models.WeekIntent.user_id == ALICE))).scalar_one()
    assert row.choice == choice
    assert any(alert for alert in harness.session.alerts() if alert), "the choice is confirmed in an alert"
    assert word in harness.session.all_text(ADMIN_ID), "Mila is told about the choice"


async def test_intent_for_an_unknown_week_is_ignored(harness: Harness, db_session: AsyncSession) -> None:
    await harness.callback(ALICE, "intent:99:take")
    assert await count(db_session, models.WeekIntent) == 0
    assert any(isinstance(m, AnswerCallbackQuery) for m in harness.session.calls), "the button is still answered"


async def test_level_button_upgrades_minimum_to_maximum(harness: Harness, db_session: AsyncSession) -> None:
    await harness.text(ALICE, "Сделала минимум")
    await harness.callback(ALICE, "level:1:max")
    stamp = (await db_session.execute(select(models.Stamp).where(models.Stamp.user_id == ALICE))).scalar_one()
    assert stamp.level == "max"
    assert "максимум" in harness.session.last_text(ALICE)
    reasons = (await db_session.execute(select(models.Freeze.reason).where(models.Freeze.user_id == ALICE))).scalars()
    assert list(reasons) == ["max"], "the first maximum earns a freeze even when fixed by the button"


async def test_level_button_refuses_to_downgrade_a_maximum_with_the_right_words(
    harness: Harness, db_session: AsyncSession
) -> None:
    await harness.photo(ALICE)
    await harness.callback(ALICE, "level:1:min")
    stamp = (await db_session.execute(select(models.Stamp).where(models.Stamp.user_id == ALICE))).scalar_one()
    assert stamp.level == "max"
    alerts = [alert for alert in harness.session.alerts() if alert]
    assert alerts, "the refusal must be shown"
    assert "не понижаю" in alerts[-1], f"the participant was told the wrong reason: {alerts[-1]!r}"


async def test_level_button_without_a_report_says_so(harness: Harness) -> None:
    await harness.callback(ALICE, "level:1:max")
    alerts = [alert for alert in harness.session.alerts() if alert]
    assert alerts and "отчёта нет" in alerts[-1]


async def test_notreport_removes_the_only_stamp_and_sends_the_letter_to_mila(
    harness: Harness, db_session: AsyncSession
) -> None:
    await harness.text(ALICE, "Это вообще не отчёт")
    report = (await db_session.execute(select(models.Report))).scalar_one()
    assert await count(db_session, models.Stamp) == 1

    await harness.callback(ALICE, f"notreport:{report.id}")
    await db_session.refresh(report)
    assert report.deleted_at is not None
    assert await count(db_session, models.Stamp) == 0, "no reports left → the stamp is removed"
    assert "штамп пересчитала" in harness.session.last_text(ALICE)
    letters = [t for t in harness.session.sent_texts(ADMIN_ID) if "сначала пришло как отчёт" in t]
    assert letters and "Это вообще не отчёт" in letters[-1]
    links = (await db_session.execute(select(models.AdminLink).where(models.AdminLink.report_id == report.id))).all()
    assert links, "Mila can reply to the corrected message"


async def test_notreport_recomputes_the_level_down_to_the_remaining_text(
    harness: Harness, db_session: AsyncSession
) -> None:
    await harness.text(ALICE, "минимум сделала")
    await harness.photo(ALICE)
    photo = (await db_session.execute(select(models.Report).where(models.Report.kind == "photo"))).scalar_one()
    stamp = (await db_session.execute(select(models.Stamp))).scalar_one()
    assert stamp.level == "max"

    await harness.callback(ALICE, f"notreport:{photo.id}")
    await db_session.refresh(stamp)
    assert stamp.level == "min", "the star is recomputed from the reports that are left (DOMAIN §2)"


async def test_notreport_on_someone_elses_report_is_refused(harness: Harness, db_session: AsyncSession) -> None:
    await harness.text(ALICE, "мой отчёт")
    report = (await db_session.execute(select(models.Report))).scalar_one()
    harness.session.reset()

    await harness.callback(BOB, f"notreport:{report.id}")
    await db_session.refresh(report)
    assert report.deleted_at is None
    assert "не твой" in harness.session.last_text(BOB)
    assert await count(db_session, models.Stamp) == 1


async def test_more_menu_buttons(harness: Harness, db_session: AsyncSession) -> None:
    await harness.text(ALICE, "⋯ Ещё")
    assert {"more:journal", "more:write", "more:help"} <= {d for _, d in harness.session.buttons(ALICE) if d}

    await harness.callback(ALICE, "more:journal")
    assert "Так он выглядит сейчас" in harness.session.last_text(ALICE)
    await harness.callback(ALICE, "more:help")
    assert "Если что-то пошло не так" in harness.session.last_text(ALICE)
    assert await dialog_row(db_session, ALICE) is None, "help must not leave a dialog state behind"


async def test_endofseason_and_journal_buttons(harness: Harness) -> None:
    await harness.text(ALICE, "📘 Паспорт")
    assert "endofseason" in [d for _, d in harness.session.buttons(ALICE)]

    await harness.callback(ALICE, "endofseason")
    assert "Что будет в конце" in harness.session.last_text(ALICE)
    assert "journal:me" in [d for _, d in harness.session.buttons(ALICE)]

    await harness.callback(ALICE, "journal:me")
    assert "Так он выглядит сейчас" in harness.session.all_text(ALICE)


async def test_dictionary_and_facts_screens_offer_their_add_buttons(harness: Harness) -> None:
    await harness.text(ALICE, "📖 Словарь")
    assert "addword" in [d for _, d in harness.session.buttons(ALICE)]
    await harness.text(ALICE, "💡 Что узнали")
    assert "addfact" in [d for _, d in harness.session.buttons(ALICE)]
    assert "adm:delfact" not in [d for _, d in harness.session.buttons(ALICE)], "the bin is Mila's only"


# =====================================================================================
# 3. The admin panel, action by action
# =====================================================================================


async def test_panel_opens_with_every_action(harness: Harness) -> None:
    await harness.text(ADMIN_ID, "⚙️ Мила")
    data = {d for _, d in harness.session.buttons(ADMIN_ID) if d}
    assert {
        "adm:draft",
        "adm:edit",
        "adm:summary",
        "adm:core",
        "adm:people:badge:0",
        "adm:people:freeze:0",
        "adm:people:wish:0",
        "addfact",
        "adm:delfact",
        "adm:remind",
        "adm:toggle",
        "adm:who",
    } <= data


async def test_panel_draft_summary_core_and_who(harness: Harness) -> None:
    await harness.photo(ALICE, caption="тако удались")
    harness.session.reset()

    await harness.callback(ADMIN_ID, "adm:draft")
    assert "#мексика" in harness.session.last_text(ADMIN_ID) or "[" in harness.session.last_text(ADMIN_ID)

    await harness.callback(ADMIN_ID, "adm:summary")
    summary_text = harness.session.last_text(ADMIN_ID)
    assert "Сдали" in summary_text and "Алиса" in summary_text

    await harness.callback(ADMIN_ID, "adm:core")
    assert "дро" in harness.session.last_text(ADMIN_ID)  # «Ядро» / «ядро»

    await harness.callback(ADMIN_ID, "adm:who")
    assert "Алиса" in harness.session.last_text(ADMIN_ID)

    await harness.callback(ADMIN_ID, "adm:panel")
    assert "adm:draft" in [d for _, d in harness.session.buttons(ADMIN_ID)]


async def test_panel_remind_sends_to_whoever_took_the_week(harness: Harness) -> None:
    await harness.text(ALICE, "📋 Задание")
    await harness.callback(ALICE, "intent:1:take")
    harness.session.reset()

    await harness.callback(ADMIN_ID, "adm:remind")
    assert harness.telegram.sent_messages and harness.telegram.sent_messages[0][0] == ALICE
    assert "1 из 1" in harness.session.last_text(ADMIN_ID)


async def test_panel_toggle_flips_the_setting_both_ways(harness: Harness, db_session: AsyncSession) -> None:
    await harness.callback(ADMIN_ID, "adm:toggle")
    assert (await db_session.get(models.Setting, "reminders_enabled")).value == "off"
    labels = [label for label, _ in harness.session.buttons(ADMIN_ID)]
    assert any("выкл" in label for label in labels)

    await harness.callback(ADMIN_ID, "adm:toggle")
    assert (await db_session.get(models.Setting, "reminders_enabled")).value == "on"
    labels = [label for label, _ in harness.session.buttons(ADMIN_ID)]
    assert any("вкл" in label for label in labels)


async def test_people_pagination_keeps_back_reachable(harness: Harness, db_session: AsyncSession) -> None:
    for index in range(25):
        await people.upsert_user(
            db_session,
            TelegramUser(id=5000 + index, username=f"p{index}", first_name=f"Человек{index}"),
            now=harness.now,
        )
    await db_session.flush()
    harness.session.reset()

    await harness.callback(ADMIN_ID, "adm:people:badge:0")
    page0 = harness.session.buttons(ADMIN_ID)
    assert len([d for _, d in page0 if d and d.startswith("adm:badge:")]) == 20
    assert "adm:people:badge:1" in [d for _, d in page0], "«дальше ›» must be there"
    assert "adm:panel" in [d for _, d in page0], "«назад» is always reachable (DOMAIN §10.7)"
    assert f"adm:badge:{ADMIN_ID}" not in [d for _, d in page0], "Mila is not in her own list"

    await harness.callback(ADMIN_ID, "adm:people:badge:1")
    page1 = harness.session.buttons(ADMIN_ID)
    assert 0 < len([d for _, d in page1 if d and d.startswith("adm:badge:")]) <= 20
    assert "adm:people:badge:0" in [d for _, d in page1], "«‹ раньше» must be there"
    assert "adm:people:badge:2" not in [d for _, d in page1], "the last page has no «дальше»"
    assert "adm:panel" in [d for _, d in page1]


async def test_panel_badge_gives_a_catalogue_achievement_once(harness: Harness, db_session: AsyncSession) -> None:
    await harness.text(ALICE, "привет")
    harness.session.reset()

    await harness.callback(ADMIN_ID, f"adm:badge:{ALICE}")
    offered = [d for _, d in harness.session.buttons(ADMIN_ID) if d]
    assert f"adm:give:{ALICE}:повар" in offered
    assert "adm:people:badge:0" in offered, "«назад» from the catalogue"

    await harness.callback(ADMIN_ID, f"adm:give:{ALICE}:повар")
    row = (await db_session.execute(select(models.Achievement))).scalar_one()
    assert row.code == "повар" and row.user_id == ALICE and row.awarded_by == ADMIN_ID
    assert "Повар" in harness.session.last_text(ALICE), "the participant is told"

    await harness.callback(ADMIN_ID, f"adm:give:{ALICE}:повар")
    assert await count(db_session, models.Achievement) == 1, "a repeat must not duplicate"
    assert "уже есть" in harness.session.last_text(ADMIN_ID)


@pytest.mark.parametrize("reason", ["comment", "meetup", "friend"])
async def test_panel_freeze_reasons(harness: Harness, db_session: AsyncSession, reason: str) -> None:
    await harness.text(ALICE, "привет")
    harness.session.reset()

    await harness.callback(ADMIN_ID, f"adm:freeze:{ALICE}")
    offered = [d for _, d in harness.session.buttons(ADMIN_ID) if d]
    assert f"adm:frz:{ALICE}:{reason}" in offered
    assert "adm:people:freeze:0" in offered

    await harness.callback(ADMIN_ID, f"adm:frz:{ALICE}:{reason}")
    row = (await db_session.execute(select(models.Freeze))).scalar_one()
    assert row.reason == reason and row.granted_by == ADMIN_ID
    assert "заморозок: 3" in harness.session.last_text(ADMIN_ID), "2 base + 1 earned"
    assert "заморозка" in harness.session.all_text(ALICE).lower()


async def test_panel_freeze_stops_at_the_ceiling_of_five(harness: Harness, db_session: AsyncSession) -> None:
    await harness.text(ALICE, "привет")
    for reason in ("comment", "meetup", "friend"):
        await harness.callback(ADMIN_ID, f"adm:frz:{ALICE}:{reason}")
    harness.session.reset()

    await harness.callback(ADMIN_ID, f"adm:frz:{ALICE}:comment")
    assert await count(db_session, models.Freeze) == 3, "2 base + 3 earned is the ceiling (DOMAIN §3)"
    assert "потолок — 5" in harness.session.last_text(ADMIN_ID)


async def test_panel_delfact_lists_and_removes(harness: Harness, db_session: AsyncSession) -> None:
    await harness.text(ADMIN_ID, "/факт Тескатлипока — дымящееся зеркало")
    fact = (await db_session.execute(select(models.Fact))).scalar_one()
    harness.session.reset()

    await harness.callback(ADMIN_ID, "adm:delfact")
    assert f"adm:delfact:{fact.id}" in [d for _, d in harness.session.buttons(ADMIN_ID)]

    await harness.callback(ADMIN_ID, f"adm:delfact:{fact.id}")
    await db_session.refresh(fact)
    assert fact.deleted_at is not None
    assert "Осталось: 0" in harness.session.last_text(ADMIN_ID)

    await harness.callback(ADMIN_ID, "adm:delfact")
    assert "Фактов пока нет" in harness.session.last_text(ADMIN_ID)


async def test_admin_edit_hides_weeks_that_are_already_over(harness: Harness) -> None:
    harness.set_now(moscow(*WEEK2, 12))
    await harness.callback(ADMIN_ID, "adm:edit")
    offered = [d for _, d in harness.session.buttons(ADMIN_ID) if d and d.startswith("adm:week:")]
    assert "adm:week:1" not in offered, "past weeks are not edited (DOMAIN §1)"
    assert "adm:week:2" in offered and "adm:week:12" in offered


# =====================================================================================
# 4. Admin commands, Russian and English
# =====================================================================================


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("/results", "Сдали"),
        ("/core", "дро"),
        ("/who", "Мила"),
        ("/badges", "Повар"),
        ("/ачивки", "Повар"),
        ("/факты", "Что мы узнали"),
        ("/facts", "Что мы узнали"),
        ("/помощь", "Если что-то пошло не так"),
        ("/журнал", "журнал"),
    ],
)
async def test_admin_command_aliases(harness: Harness, command: str, expected: str) -> None:
    await harness.text(ADMIN_ID, command)
    assert expected.lower() in harness.session.all_text(ADMIN_ID).lower(), f"{command} produced nothing useful"


async def test_results_with_a_week_number(harness: Harness) -> None:
    await harness.text(ADMIN_ID, "/results 2")
    assert "Неделя 2" in harness.session.last_text(ADMIN_ID)
    await harness.text(ADMIN_ID, "/results 99")
    assert "Недели 99" in harness.session.last_text(ADMIN_ID)


async def test_fact_and_fact_remove_commands_including_the_glued_form(
    harness: Harness, db_session: AsyncSession
) -> None:
    await harness.text(ADMIN_ID, "/факт Мешика — самоназвание ацтеков")
    fact = (await db_session.execute(select(models.Fact))).scalar_one()
    assert "Фактов за сезон" in harness.session.last_text(ADMIN_ID)

    await harness.text(ADMIN_ID, f"/факт-{fact.id}")  # no space, the legacy habit
    await db_session.refresh(fact)
    assert fact.deleted_at is not None
    assert "Убрала" in harness.session.last_text(ADMIN_ID)

    await harness.text(ADMIN_ID, "/fact- 999")
    assert "Такого факта нет" in harness.session.last_text(ADMIN_ID)
    await harness.text(ADMIN_ID, "/факт")
    assert "/факт текст" in harness.session.last_text(ADMIN_ID)


async def test_badge_by_reply_to_a_forwarded_report(harness: Harness, db_session: AsyncSession) -> None:
    await harness.photo(ALICE, caption="тако удались")
    header = [m for m in harness.session.messages(ADMIN_ID) if "Отчёт за неделю" in (m.text or "")][-1]

    await harness.text(ADMIN_ID, "/badge повар", reply_to=harness.admin_message(header))
    row = (await db_session.execute(select(models.Achievement))).scalar_one()
    assert row.user_id == ALICE and row.code == "повар"


async def test_badge_without_a_target_or_a_code_explains_itself(harness: Harness, db_session: AsyncSession) -> None:
    await harness.text(ADMIN_ID, "/ачивка")
    assert "Не поняла, кому" in harness.session.last_text(ADMIN_ID)
    await harness.text(ALICE, "привет")
    await harness.text(ADMIN_ID, "/ачивка @u1001")
    assert "Не поняла, какую" in harness.session.last_text(ADMIN_ID)
    assert await count(db_session, models.Achievement) == 0


async def test_wish_command_with_a_username(harness: Harness, db_session: AsyncSession) -> None:
    await harness.text(ALICE, "привет")
    await harness.text(ADMIN_ID, "/пожелание @u1001 Ты держала всю осень")
    wish = (await db_session.execute(select(models.Wish))).scalar_one()
    assert wish.user_id == ALICE and wish.text == "Ты держала всю осень"
    assert "Записала для" in harness.session.last_text(ADMIN_ID)

    await harness.text(ADMIN_ID, "/wish")
    assert "пожелание" in harness.session.last_text(ADMIN_ID).lower()


async def test_reminders_command_toggles_without_the_panel(harness: Harness, db_session: AsyncSession) -> None:
    await harness.text(ADMIN_ID, "/reminders")
    assert (await db_session.get(models.Setting, "reminders_enabled")).value == "off"
    assert harness.session.last_markup(ADMIN_ID) is None, "the plain command answers without the panel"


async def test_remind_command_delivers_through_the_bot(harness: Harness) -> None:
    await harness.callback(ALICE, "intent:1:take")
    harness.session.reset()
    await harness.text(ADMIN_ID, "/remind")
    assert any(m.chat_id == ALICE for m in harness.session.calls if isinstance(m, SendMessage))
    assert "1 из 1" in harness.session.last_text(ADMIN_ID)


async def test_unknown_command_is_answered_politely(harness: Harness, db_session: AsyncSession) -> None:
    await harness.text(ALICE, "/такойкомандынет")
    assert "Такой команды нет" in harness.session.last_text(ALICE)
    assert await count(db_session, models.Report) == 0, "a command is never a report"


async def test_whoami_returns_the_id(harness: Harness) -> None:
    await harness.text(ALICE, "/whoami")
    assert str(ALICE) in harness.session.last_text(ALICE)


# =====================================================================================
# 5. A participant never sees Mila's output
# =====================================================================================


@pytest.mark.parametrize(
    "command", ["/results", "/core", "/who", "/remind", "/badges", "/badge Алиса повар", "/факт что-то", "/reminders"]
)
async def test_non_admin_commands_are_refused(harness: Harness, db_session: AsyncSession, command: str) -> None:
    await harness.text(ALICE, command)
    assert harness.session.last_text(ALICE) == "Это команда Милы. Тебе — кнопки внизу 👇"
    assert await count(db_session, models.Fact) == 0
    assert await count(db_session, models.Achievement) == 0
    assert await count(db_session, models.Report) == 0


@pytest.mark.parametrize("data", ["adm:panel", "adm:summary", "adm:who", f"adm:give:{ALICE}:повар", "adm:toggle"])
async def test_non_admin_callbacks_are_refused(harness: Harness, db_session: AsyncSession, data: str) -> None:
    await harness.callback(ALICE, data)
    assert harness.session.alerts() == ["Это кнопки Милы."]
    assert not harness.session.sent_texts(ALICE), "nothing from the panel may leak"
    assert await count(db_session, models.Achievement) == 0
    assert await count(db_session, models.Setting) == 0


async def test_participant_keyboard_has_no_admin_button(harness: Harness) -> None:
    await harness.text(ALICE, "/start")
    markup = harness.session.messages(ALICE)[-1].reply_markup
    labels = [b.text for row in markup.keyboard for b in row]
    assert "⚙️ Мила" not in labels
    assert "📋 Задание" in labels


async def test_admin_keyboard_has_the_panel_button(harness: Harness) -> None:
    await harness.text(ADMIN_ID, "/start")
    markup = harness.session.messages(ADMIN_ID)[-1].reply_markup
    labels = [b.text for row in markup.keyboard for b in row]
    assert "⚙️ Мила" in labels


# =====================================================================================
# 6. Reports of every kind
# =====================================================================================


@pytest.mark.parametrize(
    ("sender", "kind", "level"),
    [
        ("photo", "photo", "max"),
        ("video", "video", "max"),
        ("video_note", "video_note", "max"),
        ("document", "document", "max"),
        ("voice", "voice", "min"),
        ("audio", "audio", "min"),
    ],
)
async def test_report_kinds_and_levels(
    harness: Harness, db_session: AsyncSession, sender: str, kind: str, level: str
) -> None:
    await getattr(harness, sender)(ALICE)
    report = (await db_session.execute(select(models.Report))).scalar_one()
    assert report.kind == kind and report.level == level
    stamp = (await db_session.execute(select(models.Stamp))).scalar_one()
    assert stamp.level == level and stamp.source == "report"
    assert stamp.week_title_snapshot == "За столом", "the week title is frozen in the stamp (DOMAIN §1)"


@pytest.mark.parametrize("sender", ["sticker", "location"])
async def test_sticker_and_location_are_not_reports(harness: Harness, db_session: AsyncSession, sender: str) -> None:
    await getattr(harness, sender)(ALICE)
    assert "Не поняла" in harness.session.last_text(ALICE)
    assert await count(db_session, models.Report) == 0
    assert await count(db_session, models.Stamp) == 0


async def test_photo_caption_becomes_the_report_text(harness: Harness, db_session: AsyncSession) -> None:
    await harness.photo(ALICE, caption="кофе де олья, вышло крепко")
    report = (await db_session.execute(select(models.Report))).scalar_one()
    assert report.text == "кофе де олья, вышло крепко"
    assert "кофе де олья" in harness.session.all_text(ADMIN_ID), "the caption travels in the header"


async def test_a_later_text_never_downgrades_the_star(harness: Harness, db_session: AsyncSession) -> None:
    await harness.photo(ALICE)
    await harness.text(ALICE, "а ещё написала пару строк")
    stamp = (await db_session.execute(select(models.Stamp))).scalar_one()
    assert stamp.level == "max", "the maximum is never downgraded by a later minimum (DOMAIN §2)"
    assert await count(db_session, models.Report) == 2, "the whole history is kept"


async def test_admin_copy_uses_copy_message_only_for_attachments(harness: Harness, db_session: AsyncSession) -> None:
    await harness.text(ALICE, "просто текст")
    assert not [m for m in harness.session.calls if isinstance(m, CopyMessage)]
    header = harness.session.messages(ADMIN_ID)[-1]
    link = await db_session.get(models.AdminLink, (ADMIN_ID, harness.session.message_id_of(header)))
    assert link is not None and link.user_id == ALICE and link.report_id is not None

    await harness.photo(ALICE)
    copies = [m for m in harness.session.calls if isinstance(m, CopyMessage)]
    assert len(copies) == 1 and copies[0].chat_id == ADMIN_ID
    copy_link = await db_session.get(models.AdminLink, (ADMIN_ID, harness.session.message_id_of(copies[0])))
    assert copy_link is not None, "replying to the copied photo must also reach the author"


async def test_mila_reply_to_the_copied_attachment_reaches_the_author(
    harness: Harness, db_session: AsyncSession
) -> None:
    await harness.photo(ALICE, caption="тако")
    copy = next(m for m in harness.session.calls if isinstance(m, CopyMessage))
    await harness.text(ADMIN_ID, "Красота!", reply_to=harness.admin_message(copy))
    assert "Мила ответила" in harness.session.last_text(ALICE)
    assert "Отправила" in harness.session.last_text(ADMIN_ID)


async def test_mila_reply_to_an_unlinked_message_is_a_report_not_a_relay(
    harness: Harness, db_session: AsyncSession
) -> None:
    unknown = Message(
        message_id=999_999, date=harness.now, chat=Chat(id=ADMIN_ID, type="private"), text="ничьё сообщение"
    )
    await harness.text(ADMIN_ID, "ответ в пустоту", reply_to=unknown)
    assert not harness.session.sent_texts(ALICE)
    report = (await db_session.execute(select(models.Report))).scalar_one()
    assert report.user_id == ADMIN_ID, "Mila writing to herself is an ordinary report"


async def test_a_report_from_mila_is_not_copied_back_to_her(harness: Harness, db_session: AsyncSession) -> None:
    await harness.photo(ADMIN_ID)
    assert not [m for m in harness.session.calls if isinstance(m, CopyMessage)]
    assert await count(db_session, models.AdminLink) == 0


# =====================================================================================
# 7. Outside a week
# =====================================================================================


@pytest.mark.parametrize(("year", "month", "day"), [(2026, 8, 25), (2026, 11, 20)])
async def test_message_outside_a_week_is_stored_and_forwarded(
    db_session: AsyncSession, tmp_path, monkeypatch: pytest.MonkeyPatch, year: int, month: int, day: int
) -> None:
    harness = await build_harness(db_session, tmp_path, monkeypatch, now=moscow(year, month, day))
    await harness.text(ALICE, "Привет, пишу вне недели")
    assert "неделя сезона не идёт" in harness.session.last_text(ALICE)
    forwarded = harness.session.sent_texts(ADMIN_ID)
    assert forwarded and "Привет, пишу вне недели" in forwarded[-1]
    report = (await db_session.execute(select(models.Report))).scalar_one()
    assert report.week_id is None and report.kind == "other"
    assert await count(db_session, models.Stamp) == 0

    header = harness.session.messages(ADMIN_ID)[-1]
    await harness.text(ADMIN_ID, "Привет!", reply_to=harness.admin_message(header))
    assert "Мила ответила" in harness.session.last_text(ALICE)


async def test_task_and_passport_outside_a_week(
    db_session: AsyncSession, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await build_harness(db_session, tmp_path, monkeypatch, now=moscow(2026, 8, 25))
    await harness.text(ALICE, "📋 Задание")
    assert "Ближайшее задание — в понедельник" in harness.session.last_text(ALICE)
    await harness.text(ALICE, "📘 Паспорт")
    assert "Штампов: <b>0</b>" in harness.session.last_text(ALICE)


# =====================================================================================
# 8. Media download failures
# =====================================================================================


async def test_a_broken_download_still_stamps_and_queues_a_job(
    db_session: AsyncSession, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await build_harness(db_session, tmp_path, monkeypatch, telegram=FakeTelegram(broken=True))
    await harness.photo(ALICE, caption="тако")
    assert "максимум" in harness.session.last_text(ALICE).lower()
    stamp = (await db_session.execute(select(models.Stamp))).scalar_one()
    assert stamp.level == "max", "a Telegram outage must not cost the participant the stamp"

    media = (await db_session.execute(select(models.Media))).scalar_one()
    assert media.downloaded_at is None
    job = (await db_session.execute(select(models.Job))).scalar_one()
    assert job.kind == "media_download" and job.payload == {"media_id": str(media.id)}
    assert job.status == "queued"


# =====================================================================================
# 9. Long texts
# =====================================================================================


async def test_long_screens_are_split_without_empty_pieces(harness: Harness, db_session: AsyncSession) -> None:
    for index in range(80):
        await harness.text(ADMIN_ID, f"/факт Факт {index}: " + "довольно длинная строчка про Мексику " * 5)
    harness.session.reset()

    await harness.text(ALICE, "💡 Что узнали")
    pieces = harness.session.sent_texts(ALICE)
    assert len(pieces) >= 2, "80 long facts do not fit into one Telegram message"
    assert all(0 < len(piece) <= 4096 for piece in pieces), "no empty and no over-long pieces"
    assert all(piece.strip() for piece in pieces)
    assert "Факт 0" in pieces[0] and "Факт 79" in pieces[-1], "nothing is lost in the middle"
    assert harness.session.last_markup(ALICE) is not None, "the keyboard rides on the last piece"


# =====================================================================================
# 10-11. Two weeks in a row: the passport chain and the freezes
# =====================================================================================


async def test_two_weeks_in_a_row_build_the_chain(harness: Harness, db_session: AsyncSession) -> None:
    await harness.photo(ALICE)  # week 1, maximum
    harness.set_now(moscow(*WEEK2, 12))
    await harness.text(ALICE, "неделя два, минимум")  # week 2, minimum
    harness.session.reset()

    await harness.text(ALICE, "📘 Паспорт")
    passport = harness.session.last_text(ALICE)
    assert "⭐  1. За столом" in passport
    assert "✅  2. Красками" in passport
    assert "Штампов: <b>2</b> из 12" in passport
    assert "со звёздочкой: 1" in passport
    assert "Статус: <b>Турист</b>" in passport
    assert "Заморозок осталось: 3 из 3" in passport, "2 base + 1 for the first maximum"
    assert "Дальше ещё 10 недель" in passport

    stamps = (await db_session.execute(select(models.Stamp).order_by(models.Stamp.week_id))).scalars().all()
    assert [s.level for s in stamps] == ["max", "min"]
    assert [s.week_title_snapshot for s in stamps] == ["За столом", "Красками"]


async def test_a_skipped_week_is_covered_by_a_freeze(harness: Harness, db_session: AsyncSession) -> None:
    await harness.photo(ALICE)  # week 1 stamped
    harness.set_now(moscow(2026, 9, 16, 12))  # week 3; week 2 was silent
    await harness.text(ALICE, "неделя три")
    harness.session.reset()

    await harness.text(ALICE, "📘 Паспорт")
    passport = harness.session.last_text(ALICE)
    assert "❄️  2. Красками · заморозка" in passport, "a gap is closed by a freeze (DOMAIN §3)"
    assert "Заморозок осталось: 2 из 3" in passport
    assert "Цепочка от этого не рвётся" in passport


async def test_today_shows_the_word_and_the_tzolkin(harness: Harness) -> None:
    await harness.text(ALICE, "🌤 Сегодня")
    today = harness.session.last_text(ALICE)
    assert "Акбаль" in today and "antojo" in today

    harness.set_now(moscow(2026, 9, 16, 12))  # week 3 has no word of its own
    await harness.text(ALICE, "🌤 Сегодня")
    later = harness.session.last_text(ALICE)
    assert "alebrije" in later, "the last released word is shown when the current week has none"


# =====================================================================================
# 12. Regressions found while writing this suite
# =====================================================================================


async def test_an_inline_button_ends_the_dialog_so_the_next_report_is_a_report(
    harness: Harness, db_session: AsyncSession
) -> None:
    """DOMAIN §10.8: «сброс любой командой/кнопкой».

    Without it a forgotten «✉️ Написать Миле» swallows the next real report: no row, no stamp.
    """
    await harness.callback(ALICE, "more:write")
    await harness.callback(ALICE, "intent:1:take")
    assert await dialog_row(db_session, ALICE) is None

    await harness.text(ALICE, "Сделала минимум: сварила кофе де олья")
    report = (await db_session.execute(select(models.Report))).scalar_one()
    assert report.kind == "text" and report.week_id is not None
    stamp = (await db_session.execute(select(models.Stamp))).scalar_one()
    assert stamp.level == "min"


@pytest.mark.parametrize(("opener", "state"), [("addword", "word"), ("addfact", "fact"), ("more:write", "letter")])
async def test_a_button_that_opens_a_dialog_still_opens_it(
    harness: Harness, db_session: AsyncSession, opener: str, state: str
) -> None:
    await harness.callback(ALICE, "more:write")  # a state is already pending
    await harness.callback(ALICE, opener)
    row = await dialog_row(db_session, ALICE)
    assert row is not None and row.state == state


async def test_the_panel_ends_an_unfinished_edit(harness: Harness, db_session: AsyncSession) -> None:
    await harness.callback(ADMIN_ID, "adm:field:2:title")
    await harness.callback(ADMIN_ID, "adm:panel")
    assert await dialog_row(db_session, ADMIN_ID) is None
    await harness.text(ADMIN_ID, "случайный текст")
    season = await content.active_season(db_session, today=date(2026, 9, 2))
    week = await content.week_by_number(db_session, season.id, 2)
    assert week.title == "Красками", "an abandoned edit must not rewrite the week later"


async def test_editing_a_week_that_ended_while_the_dialog_was_open_is_answered_not_crashed(
    db_session: AsyncSession, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await build_harness(db_session, tmp_path, monkeypatch, now=moscow(2026, 9, 6, 22))
    await harness.callback(ADMIN_ID, "adm:field:1:title")
    harness.advance(timedelta(hours=3))  # past midnight: week 1 is over, the dialog is still alive

    await harness.text(ADMIN_ID, "Новое название")
    assert "задним числом" in harness.session.last_text(ADMIN_ID)
    season = await content.active_season(db_session, today=date(2026, 9, 7))
    week = await content.week_by_number(db_session, season.id, 1)
    assert week.title == "За столом", "a finished week is not rewritten (DOMAIN §1)"
    assert await count(db_session, models.AuditLog) == 1, "only the season activation is in the log"


async def test_the_journal_counts_weeks_in_russian(harness: Harness) -> None:
    await harness.photo(ALICE)
    await harness.callback(ALICE, "journal:me")
    assert "Пройдено <b>1</b> неделя из 12" in harness.session.all_text(ALICE)

    harness.set_now(moscow(*WEEK2, 12))
    await harness.text(ALICE, "вторая неделя")
    await harness.callback(ALICE, "journal:me")
    assert "Пройдено <b>2</b> недели из 12" in harness.session.all_text(ALICE)


# =====================================================================================
# 13. The rest of the surface
# =====================================================================================


async def test_a_word_without_a_separator_is_still_saved(harness: Harness, db_session: AsyncSession) -> None:
    await harness.callback(ALICE, "addword")
    await harness.text(ALICE, "sobremesa")
    word = (await db_session.execute(select(models.Word))).scalar_one()
    assert word.word == "sobremesa" and word.meaning == ""
    await harness.text(ALICE, "📖 Словарь")
    assert "sobremesa" in harness.session.last_text(ALICE)


async def test_commands_are_case_insensitive_and_accept_the_bot_suffix(harness: Harness) -> None:
    await harness.text(ALICE, "/START@romantika_bot")
    assert "Романтика маршрутов" in harness.session.last_text(ALICE)
    await harness.text(ADMIN_ID, "/Results@romantika_bot 1")
    assert "Неделя 1" in harness.session.last_text(ADMIN_ID)


async def test_results_with_a_junk_argument_falls_back_to_the_current_week(harness: Harness) -> None:
    await harness.text(ADMIN_ID, "/results вчера")
    assert "Неделя 1" in harness.session.last_text(ADMIN_ID)


async def test_remind_when_nobody_took_the_week(harness: Harness) -> None:
    await harness.text(ADMIN_ID, "/remind")
    assert "Напоминать некому" in harness.session.last_text(ADMIN_ID)


async def test_admin_journal_of_another_person(harness: Harness) -> None:
    await harness.photo(ALICE, caption="тако")
    harness.session.reset()
    await harness.text(ADMIN_ID, "/журнал @u1001")
    assert "Алиса" in harness.session.last_text(ADMIN_ID)
    assert "Так он выглядит сейчас" not in harness.session.all_text(ADMIN_ID), "not Mila's own journal"

    await harness.text(ADMIN_ID, "/журнал никогонет")
    assert "Не нашла такого" in harness.session.last_text(ADMIN_ID)


async def test_out_of_week_attachment_is_downloaded_and_copied(
    db_session: AsyncSession, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await build_harness(db_session, tmp_path, monkeypatch, now=moscow(2026, 8, 25))
    await harness.photo(ALICE, caption="до сезона")
    media = (await db_session.execute(select(models.Media))).scalar_one()
    assert media.downloaded_at is not None and (harness.media_root / media.path).exists()
    assert [m for m in harness.session.calls if isinstance(m, CopyMessage)], "the photo reaches Mila too"
    report = (await db_session.execute(select(models.Report))).scalar_one()
    assert report.kind == "other" and report.week_id is None


async def test_summary_names_who_took_the_week_and_stayed_silent(harness: Harness, db_session: AsyncSession) -> None:
    await harness.callback(ALICE, "intent:1:take")
    await harness.callback(BOB, "intent:1:take")
    await harness.photo(ALICE, caption="готово")
    harness.session.reset()

    await harness.text(ADMIN_ID, "/results")
    text = harness.session.last_text(ADMIN_ID)
    assert "Взялись (2)" in text and "Сдали (1)" in text
    assert "⭐ Алиса" in text
    assert "Боб" in text.split("Сдали")[1], "Bob is listed among those who took it and stayed silent"

    await harness.text(ADMIN_ID, "/who")
    who = harness.session.last_text(ADMIN_ID)
    assert "Алиса" in who and "Боб" in who


async def test_core_counts_a_two_week_chain(harness: Harness, db_session: AsyncSession) -> None:
    await harness.photo(ALICE)
    await harness.text(BOB, "минимум")
    harness.set_now(moscow(*WEEK2, 12))
    await harness.text(ALICE, "вторая неделя")
    harness.session.reset()

    await harness.text(ADMIN_ID, "/core")
    core = harness.session.last_text(ADMIN_ID)
    best, current = core.split("В строю сейчас:")
    assert "По лучшей цепочке: 1" in best and "Алиса (@u1001) — 2 нед." in best
    assert "Боб" not in best, "one week is not a chain (DOMAIN §5)"
    assert "Алиса (@u1001) — 2 нед." in current, "Alice is in the core by the current streak too"


async def test_facts_screen_shows_ids_to_mila_and_numbers_to_a_participant(
    harness: Harness, db_session: AsyncSession
) -> None:
    await harness.text(ADMIN_ID, "/факт Мешика — самоназвание ацтеков")
    fact = (await db_session.execute(select(models.Fact))).scalar_one()
    harness.session.reset()

    await harness.text(ADMIN_ID, "💡 Что узнали")
    assert f"<code>{fact.id}</code>" in harness.session.last_text(ADMIN_ID)
    assert "adm:delfact" in [d for _, d in harness.session.buttons(ADMIN_ID)]

    await harness.text(ALICE, "💡 Что узнали")
    participant = harness.session.last_text(ALICE)
    assert "<b>1.</b>" in participant and f"<code>{fact.id}</code>" not in participant


async def test_a_week_before_joining_does_not_spend_a_freeze(harness: Harness, db_session: AsyncSession) -> None:
    harness.set_now(moscow(*WEEK2, 12))
    await harness.text(BOB, "пришёл только на второй неделе")
    harness.session.reset()

    await harness.text(BOB, "📘 Паспорт")
    passport = harness.session.last_text(BOB)
    assert "Заморозок осталось: 2 из 2" in passport, "week 1 was before Bob joined (DOMAIN §3)"
    assert "Штампов: <b>1</b> из 12" in passport


async def test_task_screen_names_the_deadline_and_the_word(harness: Harness) -> None:
    await harness.text(ALICE, "📋 Задание")
    task = harness.session.last_text(ALICE)
    assert "Неделя 1 · За столом" in task
    assert "воскресенье" in task and "18:00" in task
    assert "antojo" in task


async def test_a_second_report_does_not_claim_the_star_was_lost(harness: Harness, db_session: AsyncSession) -> None:
    """The answer names both: this report is a minimum, and the week's star stays (DOMAIN §2).

    Before the fix a text sent after a photo answered «✅ Записала как минимум — штамп за
    неделю», which reads as «the star is gone», while the stamp in the database stayed ⭐.
    """
    await harness.photo(ALICE)
    await harness.text(ALICE, "а ещё написала пару строк")

    stamp = (await db_session.execute(select(models.Stamp))).scalar_one()
    assert stamp.level == "max"
    reply = harness.session.last_text(ALICE)
    assert "Записала как <b>минимум</b>" in reply, "the text itself counts as a minimum"
    assert "Звёздочка" in reply and "остаётся" in reply, "and the receipt says the star stays"
    assert "+1 заморозка" not in reply, "the freeze is granted once"

    labels = [label for label, _ in harness.session.buttons(ALICE)]
    assert "Это был минимум" not in labels, "a star never goes down, so the button would be dead"
    assert any("не отчёт" in label for label in labels)
    await harness.callback(ALICE, "level:1:min")  # from an older message: still answered, not applied
    alerts = [alert for alert in harness.session.alerts() if alert]
    assert "не понижаю" in alerts[-1], "and pressing it explains the rule"


async def test_summary_without_a_running_week_asks_for_a_number(
    db_session: AsyncSession, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await build_harness(db_session, tmp_path, monkeypatch, now=moscow(2026, 8, 25))
    await harness.callback(ADMIN_ID, "adm:summary")
    assert "Укажи номер" in harness.session.last_text(ADMIN_ID)
    await harness.callback(ADMIN_ID, "adm:draft")
    assert "неделя сезона не идёт" in harness.session.last_text(ADMIN_ID)


async def test_the_journal_shows_the_photos_that_were_sent(harness: Harness) -> None:
    await harness.photo(ALICE, caption="тако")
    harness.session.reset()
    await harness.callback(ALICE, "journal:me")
    photos = [m for m in harness.session.calls if type(m).__name__ == "SendPhoto"]
    assert len(photos) == 1 and photos[0].chat_id == ALICE


async def test_free_text_achievement_and_html_are_escaped(harness: Harness, db_session: AsyncSession) -> None:
    await harness.text(ALICE, "<b>жирный</b> отчёт & прочее")
    header = [t for t in harness.session.sent_texts(ADMIN_ID) if "Отчёт за неделю" in t][-1]
    assert "&lt;b&gt;жирный&lt;/b&gt;" in header and "&amp;" in header

    await harness.text(ADMIN_ID, "/ачивка @u1001 <i>За стойкость</i>")
    row = (await db_session.execute(select(models.Achievement))).scalar_one()
    assert row.label == "<i>За стойкость</i>"
    assert "&lt;i&gt;За стойкость&lt;/i&gt;" in harness.session.last_text(ALICE), "shown as text, not as markup"


async def test_a_participant_pressing_the_panel_button_gets_nothing(harness: Harness, db_session: AsyncSession) -> None:
    await harness.text(ALICE, "⚙️ Мила")
    assert harness.session.last_text(ALICE) == "Это команда Милы. Тебе — кнопки внизу 👇"
    assert harness.session.last_markup(ALICE) is None
    assert await count(db_session, models.Report) == 0
