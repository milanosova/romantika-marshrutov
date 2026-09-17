"""`python -m romantika.ops.chat_mockup --scenario start --out brain/tasks/NN/before`.

Plays a scenario against the fake Bot API of the local stand (a participant sends commands,
texts, photos, presses buttons), collects what the bot really answered and renders the
conversation as a Telegram-like HTML mock-up (plus a PNG when Google Chrome is installed).

Used for the «было — стало» pairs in plans and reports: the texts are the bot's own, not
retyped. Scenarios: built-in names below, or a JSON file with a list of steps:
    [{"send": "/start"}, {"send": "Отчёт: посмотрела фильм"}, {"photo": "С котом"}, {"press": "максимум"}]
`press` finds the newest inline button whose text contains the given fragment.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
QUIET_SECONDS = 1.5
MAX_WAIT_SECONDS = 12.0

SCENARIOS: dict[str, list[dict[str, str]]] = {
    "start": [{"send": "/start"}],
    "task": [{"send": "/start"}, {"send": "/task"}],
    "report-text": [{"send": "/start"}, {"send": "/task"}, {"send": "Отчёт: посмотрела фильм и записала три слова."}],
    "report-photo": [{"send": "/start"}, {"send": "/task"}, {"photo": "Готово, вот моя тарелка"}],
    "passport": [{"send": "/start"}, {"send": "/passport"}],
    "journal": [{"send": "/start"}, {"send": "/journal"}],
    "words": [{"send": "/start"}, {"send": "/words"}],
    "help": [{"send": "/start"}, {"send": "/help"}],
}

TAG_RE = re.compile(r"</?([a-zA-Z][a-zA-Z0-9-]*)\b[^>]*>")
SENTINEL_RE = re.compile("\x00\\d+\x01")
#: Telegram HTML the bot may send → the tag we re-emit (attributes are always dropped).
ALLOWED_TAGS = {"b": "b", "strong": "b", "i": "i", "em": "i", "u": "u", "s": "s", "code": "code", "pre": "pre"}

STYLE = """
body{margin:0;background:#8FB0D1;font:15px/1.4 -apple-system,"Segoe UI",Roboto,sans-serif;color:#111}
.phone{width:420px;margin:0 auto;background:#DDE6EF;min-height:100vh;display:flex;flex-direction:column}
.top{background:#517DA2;color:#fff;padding:12px 16px;font-weight:600}
.top small{display:block;font-weight:400;opacity:.85;font-size:12px}
.chat{padding:12px 10px;display:flex;flex-direction:column;gap:6px;flex:1}
.msg{max-width:82%;border-radius:12px;padding:8px 10px;background:#fff;box-shadow:0 1px 1px rgba(0,0,0,.08);
  white-space:normal;word-wrap:break-word}
.msg.user{align-self:flex-end;background:#EFFDDE}
.msg .text b{font-weight:600} .msg .text code{font-family:ui-monospace,Menlo,monospace;font-size:13px}
.photo{background:linear-gradient(135deg,#c9d6a3,#7fa07a);height:120px;border-radius:8px;margin-bottom:6px;color:#fff;
  display:flex;align-items:center;justify-content:center;font-size:12px;letter-spacing:.08em;text-transform:uppercase}
.inline{display:flex;gap:4px;margin-top:6px}
.inline span{flex:1;text-align:center;background:#EAF3FA;border:1px solid #BFD4E6;color:#2B5278;border-radius:6px;
  padding:6px 4px;font-size:13px}
.toast{align-self:center;background:rgba(0,0,0,.55);color:#fff;border-radius:14px;padding:4px 12px;font-size:12px}
.kbd{background:#F4F4F5;padding:6px;display:flex;flex-direction:column;gap:6px;border-top:1px solid #d8dde3}
.kbd .row{display:flex;gap:6px}
.kbd span{flex:1;text-align:center;background:#fff;border-radius:6px;padding:8px 4px;font-size:13.5px;
  box-shadow:0 1px 0 rgba(0,0,0,.06)}
"""


def _clean(text: str) -> str:
    """Keep the bot's bold/italic/code as bare tags, escape everything else, keep line breaks.

    Tags are rebuilt from their name, so no attribute survives; unknown tags disappear; a tag
    left open by the bot is closed at the end so it cannot bleed into the rest of the page.
    """
    keep: dict[str, str] = {}
    open_tags: list[str] = []

    # Sentinels use two different control characters, so a key can never be assembled from the
    # tail of one key, a digit of the bot's text and the head of the next («2 из 12» once came
    # out as «45 из 12» that way); they are swapped back in a single regex pass.
    def stash(match: re.Match[str]) -> str:
        key = f"\x00{len(keep)}\x01"
        name = ALLOWED_TAGS.get(match.group(1).lower())
        if name is None:
            keep[key] = ""
        elif match.group(0).startswith("</"):
            if name in open_tags:
                open_tags.remove(name)
                keep[key] = f"</{name}>"
            else:
                keep[key] = ""
        else:
            open_tags.append(name)
            keep[key] = f"<{name}>"
        return key

    stashed = TAG_RE.sub(stash, text)
    escaped = html.escape(stashed, quote=False)
    escaped = SENTINEL_RE.sub(lambda m: keep[m.group(0)], escaped)
    escaped += "".join(f"</{name}>" for name in reversed(open_tags))
    return escaped.replace("\n", "<br>")


class Stand:
    def __init__(self, api: str, user_id: int, name: str) -> None:
        self.api = api.rstrip("/")
        self.user_id = user_id
        self.name = name
        self.client = httpx.Client(timeout=10)
        self.transcript: list[dict[str, Any]] = []
        self.last_inline: list[tuple[int, str, str]] = []  # (message_id, button text, callback data)
        self.cursor = time.time() - 1  # everything the bot sent after this instant belongs to the mock-up
        self.seen: set[tuple[int, float]] = set()  # (message_id, at) already in the transcript

    def _collect(self) -> None:
        """Wait until the bot has been quiet for QUIET_SECONDS, then absorb everything new.

        One cursor for the whole run: a message the bot or the worker sends late (after the quiet
        period of the step that caused it) is picked up by the next step instead of being lost.
        """
        deadline = time.time() + MAX_WAIT_SECONDS
        pending = 0
        last_change = time.time()
        rows: list[dict[str, Any]] = []
        while time.time() < deadline:
            rows = self.client.get(
                f"{self.api}/_control/sent", params={"chat_id": self.user_id, "since": self.cursor}
            ).json()["result"]
            fresh = len(self.absorbed(rows, dry=True))
            if fresh != pending:
                pending = fresh
                last_change = time.time()
            elif fresh and time.time() - last_change > QUIET_SECONDS:
                break
            time.sleep(0.25)
        self.absorbed(rows)

    def absorbed(self, rows: list[dict[str, Any]], *, dry: bool = False) -> list[dict[str, Any]]:
        """Turn raw `sent` rows into transcript entries, skipping what is already there."""
        entries: list[dict[str, Any]] = []
        for row in rows:
            message = row["message"]
            key = (message.get("message_id", 0), row["at"])
            if key in self.seen:
                continue
            if row["method"] == "answerCallbackQuery":
                entry: dict[str, Any] = {"who": "toast", "text": message.get("text", "")}
                if not entry["text"]:
                    if not dry:
                        self.seen.add(key)
                    continue
            else:
                entry = {"who": "bot", "text": message.get("text") or message.get("caption") or ""}
                if message.get("photo"):
                    entry["photo"] = True
                markup = message.get("reply_markup") or {}
                if isinstance(markup, str):
                    markup = json.loads(markup)
                if markup.get("inline_keyboard"):
                    entry["inline"] = [[button["text"] for button in line] for line in markup["inline_keyboard"]]
                    if not dry:
                        self.last_inline = [
                            (message["message_id"], button["text"], button.get("callback_data", ""))
                            for line in markup["inline_keyboard"]
                            for button in line
                        ] + self.last_inline
                if markup.get("keyboard"):
                    entry["keyboard"] = [
                        [button["text"] if isinstance(button, dict) else button for button in line]
                        for line in markup["keyboard"]
                    ]
            entries.append(entry)
            if not dry:
                self.seen.add(key)
                self.transcript.append(entry)
                self.cursor = max(self.cursor, float(row["at"]) - 0.001)
        return entries

    def send(self, text: str) -> None:
        self.transcript.append({"who": "user", "text": text})
        self.client.post(
            f"{self.api}/_control/text", json={"user_id": self.user_id, "text": text, "name": self.name}
        ).raise_for_status()
        self._collect()

    def photo(self, caption: str) -> None:
        self.transcript.append({"who": "user", "text": caption, "photo": True})
        png = Path(__file__).with_name("demo_data.py")  # any bytes do: the fake API only stores them
        self.client.post(
            f"{self.api}/_control/media",
            data={"user_id": str(self.user_id), "kind": "photo", "caption": caption, "name": self.name},
            files={"file": ("photo.png", png.read_bytes(), "image/png")},
        ).raise_for_status()
        self._collect()

    def press(self, fragment: str) -> None:
        for message_id, text, data in self.last_inline:
            if fragment.lower() in text.lower():
                self.transcript.append({"who": "user", "text": f"[{text}]", "press": True})
                self.client.post(
                    f"{self.api}/_control/callback",
                    json={"user_id": self.user_id, "data": data, "message_id": message_id},
                ).raise_for_status()
                self._collect()
                return
        raise SystemExit(f"no inline button containing {fragment!r}; seen: {[t for _, t, _ in self.last_inline]}")


def render(transcript: list[dict[str, Any]], *, title: str, bot_name: str) -> str:
    parts = []
    for entry in transcript:
        if entry["who"] == "toast":
            parts.append(f'<div class="toast">{_clean(entry["text"])}</div>')
            continue
        side = "user" if entry["who"] == "user" else "bot"
        body = ""
        if entry.get("photo"):
            body += '<div class="photo">фото</div>'
        body += f'<div class="text">{_clean(entry["text"])}</div>' if entry.get("text") else ""
        if entry.get("inline"):
            body += "".join(
                '<div class="inline">' + "".join(f"<span>{html.escape(b)}</span>" for b in line) + "</div>"
                for line in entry["inline"]
            )
        parts.append(f'<div class="msg {side}">{body}</div>')
    keyboard = next((entry["keyboard"] for entry in reversed(transcript) if entry.get("keyboard")), None)
    keyboard_html = ""
    if keyboard:
        keyboard_html = (
            '<div class="kbd">'
            + "".join(
                '<div class="row">' + "".join(f"<span>{html.escape(b)}</span>" for b in line) + "</div>"
                for line in keyboard
            )
            + "</div>"
        )
    return (
        f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
        f"<style>{STYLE}</style></head><body>"
        f'<div class="phone"><div class="top">{html.escape(bot_name)}'
        "<small>бот · макет переписки со стенда</small></div>"
        f'<div class="chat">{"".join(parts)}</div>{keyboard_html}</div></body></html>'
    )


def screenshot(html_path: Path, png_path: Path) -> bool:
    if not Path(CHROME).exists():
        return False
    subprocess.run(
        [
            CHROME,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--window-size=500,1100",
            f"--screenshot={png_path}",
            html_path.resolve().as_uri(),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return png_path.exists()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render a bot conversation from the local stand as an HTML/PNG mock-up"
    )
    parser.add_argument(
        "--scenario", required=True, help=f"built-in ({', '.join(SCENARIOS)}) or a JSON file with steps"
    )
    parser.add_argument("--out", type=Path, required=True, help="directory for <scenario>.html and <scenario>.png")
    parser.add_argument("--user", type=int, default=1099, help="stand user id to play as (default: a fresh one, 1099)")
    parser.add_argument("--name", default="Алиса")
    parser.add_argument("--api", default="http://127.0.0.1:8081", help="fake Bot API of the stand")
    parser.add_argument("--bot-name", default="Романтика маршрутов")
    args = parser.parse_args()

    if args.scenario in SCENARIOS:
        steps, slug = SCENARIOS[args.scenario], args.scenario
    else:
        path = Path(args.scenario)
        steps, slug = json.loads(path.read_text(encoding="utf-8")), path.stem
    stand = Stand(args.api, args.user, args.name)
    for step in steps:
        if "send" in step:
            stand.send(step["send"])
        elif "photo" in step:
            stand.photo(step["photo"])
        elif "press" in step:
            stand.press(step["press"])
        else:
            raise SystemExit(f"unknown step {step!r}")
    args.out.mkdir(parents=True, exist_ok=True)
    html_path = args.out / f"chat-{slug}.html"
    html_path.write_text(render(stand.transcript, title=f"Переписка: {slug}", bot_name=args.bot_name), encoding="utf-8")
    png_path = args.out / f"chat-{slug}.png"
    made_png = screenshot(html_path, png_path)
    print(f"{html_path}" + (f"\n{png_path}" if made_png else "  (no Chrome: PNG skipped)"))
    if shutil.which("open") and not made_png:
        print("open the HTML in a browser to view it")


if __name__ == "__main__":
    main()
