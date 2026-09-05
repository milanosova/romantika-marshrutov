"""`python -m romantika.web` — uvicorn on 0.0.0.0:8010 (published as 127.0.0.1:8010)."""

from __future__ import annotations

import uvicorn

from romantika.config import get_settings
from romantika.db.session import make_session_factory
from romantika.logging import setup_logging
from romantika.services.media import MediaStore
from romantika.web.app import create_app


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, json_output=settings.env != "dev")
    app = create_app(settings, make_session_factory(settings.database_url), MediaStore(settings.media_dir))
    uvicorn.run(app, host="0.0.0.0", port=8010, log_config=None, access_log=True, proxy_headers=True)


if __name__ == "__main__":
    main()
