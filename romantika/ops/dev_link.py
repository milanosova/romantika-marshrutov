"""`python -m romantika.ops.dev_link --user 1001 --name Алиса` — a signed Mini App link (ENV=dev only).

Prints the `initData` the API trusts (signed with BOT_TOKEN) as a URL with `?init=` and as the
header value, so a browser or an agent can act as that participant on the local stand.
"""

from __future__ import annotations

import argparse
import time
from urllib.parse import quote

from romantika.config import get_settings
from romantika.web.auth import build_init_data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", type=int, required=True)
    parser.add_argument("--name", default="Гость")
    parser.add_argument("--username", default="")
    parser.add_argument("--path", default="/app", help="/app, /app/journal, /app/admin …")
    args = parser.parse_args()
    settings = get_settings()
    if settings.env != "dev":
        raise SystemExit("dev_link signs with BOT_TOKEN — refuse to run outside ENV=dev")
    user: dict[str, object] = {"id": args.user, "first_name": args.name}
    if args.username:
        user["username"] = args.username
    init = build_init_data(settings.bot_token, user, auth_date=int(time.time()))
    print(f"{settings.public_base_url}{args.path}?init={quote(init, safe='')}")
    print(f"X-Telegram-Init-Data: {init}")


if __name__ == "__main__":
    main()
