# One image, five commands: bot | web | worker | migrate | backup (docker/entrypoint.sh).
# Built on the VPS (amd64); apt/PyPI go through the build-arg proxies from scripts/deploy.sh.
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Moscow \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# postgresql-client-16 comes from the PGDG repository: bookworm ships client 15 and pg_dump
# must not be older than the server. WeasyPrint needs pango/cairo; DejaVu + Noto Color Emoji
# give the PDF its Cyrillic and its emoji; rsync makes the hard-linked media snapshots.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg; \
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg; \
    echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] http://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        postgresql-client-16 rsync tzdata tini \
        fonts-dejavu-core fonts-noto-color-emoji \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 libffi8 libcairo2 \
        libgdk-pixbuf-2.0-0 shared-mime-info; \
    apt-get purge -y --auto-remove gnupg; \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /usr/local/bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

COPY romantika ./romantika
COPY data ./data
COPY alembic.ini Makefile ./
COPY scripts ./scripts
COPY docker/entrypoint.sh /app/docker/entrypoint.sh
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

RUN set -eux; \
    useradd --uid 1000 --create-home --shell /usr/sbin/nologin app; \
    mkdir -p /media /backups; \
    chown app:app /media /backups /app; \
    chmod +x /app/docker/entrypoint.sh /app/scripts/*.sh

ENV PATH="/app/.venv/bin:${PATH}" \
    MEDIA_DIR=/media \
    BACKUPS_DIR=/backups
USER app
ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker/entrypoint.sh"]
CMD ["web"]
