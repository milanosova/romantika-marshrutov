"""`romantika.ops.chat_mockup`: the bot's HTML survives, everything else is escaped."""

from __future__ import annotations

from romantika.ops import chat_mockup


def test_clean_keeps_bold_and_escapes_the_rest() -> None:
    # Unknown tags vanish (a script tag included); their text stays; `&` is escaped.
    assert chat_mockup._clean("Привет, <b>мир</b> & <a href='x'>ссылка</a>\n<script>x</script>") == (
        "Привет, <b>мир</b> &amp; ссылка<br>x"
    )
    assert chat_mockup._clean("1 < 2 && 3 > 2") == "1 &lt; 2 &amp;&amp; 3 &gt; 2"


def test_clean_drops_attributes_and_closes_what_the_bot_left_open() -> None:
    assert chat_mockup._clean('<b onmouseover="alert(1)" style="x">жирный</b>') == "<b>жирный</b>"
    assert chat_mockup._clean('<code class="x" onclick="y">c</code> <strong>s</strong> <em>e</em>') == (
        "<code>c</code> <b>s</b> <i>e</i>"
    )
    assert chat_mockup._clean("<b>не закрыт") == "<b>не закрыт</b>"
    assert chat_mockup._clean("лишний </b> закрывающий") == "лишний  закрывающий"


def test_clean_survives_digits_between_many_tags() -> None:
    # Regression: with «\x00N\x00» sentinels, «<b>2</b>» after four other tags rendered as «45».
    text = "<i>a</i> <i>b</i> Штампов: <b>2</b> из 12 <code>3</code> <u>4</u>"
    cleaned = chat_mockup._clean(text)
    assert "Штампов: <b>2</b> из 12" in cleaned
    assert "\x00" not in cleaned and "\x01" not in cleaned
    many = " ".join(f"<b>{n}</b>" for n in range(15))
    assert chat_mockup._clean(many) == " ".join(f"<b>{n}</b>" for n in range(15))


def test_absorbed_dedupes_by_message_and_keeps_late_messages() -> None:
    stand = chat_mockup.Stand.__new__(chat_mockup.Stand)
    stand.transcript, stand.last_inline, stand.seen, stand.cursor = [], [], set(), 0.0
    rows = [
        {"method": "sendMessage", "chat_id": 1, "at": 10.0, "message": {"message_id": 5, "text": "раз"}},
        {"method": "answerCallbackQuery", "chat_id": 1, "at": 10.5, "message": {"text": ""}},
        {
            "method": "sendMessage",
            "chat_id": 1,
            "at": 11.0,
            "message": {
                "message_id": 6,
                "text": "два",
                "reply_markup": {"inline_keyboard": [[{"text": "Ок", "callback_data": "ok"}]]},
            },
        },
    ]
    assert [e["text"] for e in stand.absorbed(rows)] == ["раз", "два"]
    assert stand.absorbed(rows) == []  # the same rows again add nothing
    late = [*rows, {"method": "sendMessage", "chat_id": 1, "at": 12.0, "message": {"message_id": 7, "text": "три"}}]
    assert [e["text"] for e in stand.absorbed(late)] == ["три"]
    assert stand.last_inline[0] == (6, "Ок", "ok") and stand.cursor > 11.9


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
