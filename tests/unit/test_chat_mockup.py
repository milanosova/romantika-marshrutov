"""`romantika.ops.chat_mockup`: the bot's HTML survives, everything else is escaped."""

from __future__ import annotations

from romantika.ops import chat_mockup


def test_clean_keeps_bold_and_escapes_the_rest() -> None:
    assert chat_mockup._clean("Привет, <b>мир</b> & <a href='x'>ссылка</a>\n<script>x</script>") == (
        "Привет, <b>мир</b> &amp; ссылка<br>&lt;script&gt;x&lt;/script&gt;"
    )


def test_render_places_user_right_and_bot_left_with_buttons() -> None:
    transcript = [
        {"who": "user", "text": "/start"},
        {"who": "bot", "text": "Привет!", "inline": [["Берусь", "Попробую"]], "keyboard": [["Задание", "Паспорт"]]},
        {"who": "toast", "text": "Записала"},
        {"who": "user", "text": "Готово", "photo": True},
    ]
    html = chat_mockup.render(transcript, title="t", bot_name="Бот")
    assert html.index('class="msg user"') < html.index('class="msg bot"')
    assert "<span>Берусь</span>" in html and "<span>Попробую</span>" in html
    assert '<div class="kbd">' in html and "<span>Задание</span>" in html
    assert 'class="toast">Записала' in html
    assert html.count('class="photo"') == 1


def test_builtin_scenarios_are_well_formed() -> None:
    for name, steps in chat_mockup.SCENARIOS.items():
        assert steps and steps[0] == {"send": "/start"}, name
        assert all(set(step) & {"send", "photo", "press"} for step in steps), name
