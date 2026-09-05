"""ORM models for the whole schema (ARCHITECTURE §4).

Enum-like columns are stored as `text` with a CHECK constraint listing the allowed values,
so a bad value fails in the database and not only in Python.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from romantika.db.base import Base, Timestamp, TimestampMixin
from romantika.domain.types import ReportKind, StampLevel

__all__ = [
    "Achievement",
    "AchievementType",
    "AdminLink",
    "AuditLog",
    "DailyKind",
    "DialogState",
    "Fact",
    "Freeze",
    "FreezeReason",
    "IntentChoice",
    "Job",
    "JobStatus",
    "Letter",
    "Media",
    "ReminderLog",
    "Report",
    "ReportKind",
    "Season",
    "SeasonMember",
    "SeasonStatus",
    "Setting",
    "Stamp",
    "StampLevel",
    "StampSource",
    "User",
    "Week",
    "WeekIntent",
    "Wish",
    "Word",
]


class SeasonStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class DailyKind(StrEnum):
    TZOLKIN = "tzolkin"


class IntentChoice(StrEnum):
    TAKE = "take"
    TRY = "try"
    SKIP = "skip"


class StampSource(StrEnum):
    REPORT = "report"
    ADMIN = "admin"


class FreezeReason(StrEnum):
    WORD = "word"
    MAX = "max"
    COMMENT = "comment"
    MEETUP = "meetup"
    FRIEND = "friend"
    MANUAL = "manual"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


def enum_check(column: str, values: type[StrEnum], name: str) -> CheckConstraint:
    """CHECK constraint accepting exactly the values of a `StrEnum`."""
    allowed = ", ".join(f"'{member.value}'" for member in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=name)


class User(Base, TimestampMixin):
    """A person, shared across seasons. `joined_at` is the first contact and never changes."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now(), nullable=False)
    blocked_at: Mapped[datetime | None] = mapped_column(Timestamp)


class Season(Base, TimestampMixin):
    """One country for about three months; exactly one season is active at a time."""

    __tablename__ = "seasons"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_seasons_slug"),
        enum_check("status", SeasonStatus, "status"),
        CheckConstraint("daily_kind IS NULL OR daily_kind IN ('tzolkin')", name="daily_kind"),
        CheckConstraint("ends_on >= starts_on", name="dates"),
        Index(
            "uq_seasons_single_active",
            "status",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    title_accusative: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    hashtag: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=SeasonStatus.DRAFT.value)
    daily_kind: Mapped[str | None] = mapped_column(String(16))
    daily_title: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    daily_note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    base_freezes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("2"))
    max_freezes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    level_tourist: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    level_traveler: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("4"))
    level_resident: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("9"))
    journal_promise_on: Mapped[date | None] = mapped_column(Date)


class Week(Base, TimestampMixin):
    """A Monday-to-Sunday week of a season; weeks of one season never overlap."""

    __tablename__ = "weeks"
    __table_args__ = (
        UniqueConstraint("season_id", "number", name="uq_weeks_season_id_number"),
        # Target of the composite foreign keys that tie a denormalized `season_id` to its week.
        UniqueConstraint("season_id", "id", name="uq_weeks_season_id_id"),
        CheckConstraint("number >= 1", name="number"),
        CheckConstraint("ends_on >= starts_on", name="dates"),
        # Weeks of one season may not overlap: `calendar.week_for` must find at most one.
        # Deferrable, so that a re-import may move the whole calendar row by row: the
        # intermediate states overlap even when the final one does not (see `services.seed`).
        ExcludeConstraint(
            ("season_id", "="),
            (text("daterange(starts_on, ends_on, '[]')"), "&&"),
            name="weeks_no_overlap",
            using="gist",
            deferrable=True,
            initially="IMMEDIATE",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False, index=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    intro: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    task_min: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    task_max: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    word: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    word_ru: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    word_meaning: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


class AchievementType(Base, TimestampMixin):
    """Catalogue of achievements of a season; they are granted by hand only."""

    __tablename__ = "achievement_types"
    __table_args__ = (UniqueConstraint("season_id", "code", name="uq_achievement_types_season_id_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    emoji: Mapped[str] = mapped_column(String(16), nullable=False, server_default="")
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class SeasonMember(Base, TimestampMixin):
    """Membership in a season, created on the first contact while the season runs."""

    __tablename__ = "season_members"
    __table_args__ = (PrimaryKeyConstraint("season_id", "user_id", name="pk_season_members"),)

    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now(), nullable=False)


class WeekIntent(Base, TimestampMixin):
    """«Берусь / Попробую / В этот раз мимо» under the weekly task."""

    __tablename__ = "intents"
    __table_args__ = (
        UniqueConstraint("user_id", "week_id", name="uq_intents_user_id_week_id"),
        # The denormalized season must be the season the week belongs to.
        ForeignKeyConstraint(
            ["season_id", "week_id"], ["weeks.season_id", "weeks.id"], name="fk_intents_season_id_week_id_weeks"
        ),
        enum_check("choice", IntentChoice, "choice"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    week_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    choice: Mapped[str] = mapped_column(String(16), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        Timestamp, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Report(Base, TimestampMixin):
    """Everything a participant sent us. Never physically deleted (`deleted_at` only)."""

    __tablename__ = "reports"
    __table_args__ = (
        # The denormalized season must be the season the week belongs to.
        ForeignKeyConstraint(
            ["season_id", "week_id"], ["weeks.season_id", "weeks.id"], name="fk_reports_season_id_week_id_weeks"
        ),
        enum_check("kind", ReportKind, "kind"),
        enum_check("level", StampLevel, "level"),
        # A Mini App submission carries a client-made id; a retry after a lost response finds
        # the row it already made instead of stamping a second report (ARCHITECTURE §8.1).
        Index(
            "uq_reports_user_id_client_id",
            "user_id",
            "client_id",
            unique=True,
            postgresql_where=text("client_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    week_id: Mapped[int | None] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    tg_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger)
    client_id: Mapped[str | None] = mapped_column(String(64))
    """Idempotency key of a Mini App submission; None for messages that came through the bot."""
    edited_at: Mapped[datetime | None] = mapped_column(Timestamp)
    """Last edit in the Mini App (text or files); the previous text is in `audit_log`."""
    deleted_at: Mapped[datetime | None] = mapped_column(Timestamp)


class Media(Base, TimestampMixin):
    """A file attached to a report. Media are immutable: hidden, never deleted."""

    __tablename__ = "media"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"), nullable=False, index=True)
    tg_file_id: Mapped[str | None] = mapped_column(Text)
    """None for a file uploaded through the Mini App until the worker first sends it to Telegram."""
    tg_file_unique_id: Mapped[str | None] = mapped_column(String(255), index=True)
    mime: Mapped[str | None] = mapped_column(String(255))
    size: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    downloaded_at: Mapped[datetime | None] = mapped_column(Timestamp)
    hidden_at: Mapped[datetime | None] = mapped_column(Timestamp)


class Stamp(Base, TimestampMixin):
    """One stamp per participant and week; the week title is frozen at award time."""

    __tablename__ = "stamps"
    __table_args__ = (
        UniqueConstraint("user_id", "week_id", name="uq_stamps_user_id_week_id"),
        # The denormalized season must be the season the week belongs to.
        ForeignKeyConstraint(
            ["season_id", "week_id"], ["weeks.season_id", "weeks.id"], name="fk_stamps_season_id_week_id_weeks"
        ),
        enum_check("level", StampLevel, "level"),
        enum_check("source", StampSource, "source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    week_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    week_title_snapshot: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    awarded_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now(), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default=StampSource.REPORT.value)


class Freeze(Base, TimestampMixin):
    """An earned bonus freeze; the base freezes of a season are a constant, not rows."""

    __tablename__ = "freezes"
    __table_args__ = (
        enum_check("reason", FreezeReason, "reason"),
        # `word` and `max` are granted by the bot once per season and participant (DOMAIN §3);
        # the partial unique index is what makes that true for concurrent workers too.
        Index(
            "uq_freezes_auto_reason",
            "season_id",
            "user_id",
            "reason",
            unique=True,
            postgresql_where=text("reason IN ('word', 'max')"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(16), nullable=False)
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(Text)


class Achievement(Base, TimestampMixin):
    """An achievement granted to a participant; never granted twice."""

    __tablename__ = "achievements"
    __table_args__ = (UniqueConstraint("season_id", "user_id", "code", name="uq_achievements_season_id_user_id_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    awarded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    awarded_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now(), nullable=False)


class Word(Base, TimestampMixin):
    """A word a participant added to the dictionary («слово — что значит»)."""

    __tablename__ = "words"
    __table_args__ = (
        # The denormalized season must be the season the week belongs to (NULL week: no check).
        ForeignKeyConstraint(
            ["season_id", "week_id"], ["weeks.season_id", "weeks.id"], name="fk_words_season_id_week_id_weeks"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    week_id: Mapped[int | None] = mapped_column(Integer, index=True)
    word: Mapped[str] = mapped_column(String(255), nullable=False)
    meaning: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


class Fact(Base, TimestampMixin):
    """«Что мы узнали про страну»; a null author means Mila wrote it."""

    __tablename__ = "facts"
    __table_args__ = (
        # The denormalized season must be the season the week belongs to (NULL week: no check).
        ForeignKeyConstraint(
            ["season_id", "week_id"], ["weeks.season_id", "weeks.id"], name="fk_facts_season_id_week_id_weeks"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False, index=True)
    week_id: Mapped[int | None] = mapped_column(Integer, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(Timestamp)


class Wish(Base, TimestampMixin):
    """Mila's personal word to a participant, one per season."""

    __tablename__ = "wishes"
    __table_args__ = (UniqueConstraint("season_id", "user_id", name="uq_wishes_season_id_user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        Timestamp, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Letter(Base, TimestampMixin):
    """A message to Mila that is not a report (DOMAIN §2): «Написать Миле» in the bot or the app,
    a message sent outside a week, or a report the author took back with «это не отчёт».

    Mila's inbox in the admin app lists these; her answer (a reply in the chat or the reply box
    in the app) is written back here, so the inbox shows what is still unanswered.
    """

    __tablename__ = "letters"
    __table_args__ = (
        CheckConstraint("source IN ('bot', 'app', 'out_of_week', 'not_report')", name="source"),
        Index("ix_letters_season_id_created_at", "season_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[int | None] = mapped_column(ForeignKey("seasons.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    report_id: Mapped[int | None] = mapped_column(ForeignKey("reports.id"))
    """The report the letter came from (out of a week, or taken back); its files stay there."""
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    reply_text: Mapped[str | None] = mapped_column(Text)
    replied_at: Mapped[datetime | None] = mapped_column(Timestamp)
    replied_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class AdminLink(Base, TimestampMixin):
    """Routing of Mila's replies back to the author of a report or a letter."""

    __tablename__ = "admin_links"
    __table_args__ = (PrimaryKeyConstraint("admin_chat_id", "admin_message_id", name="pk_admin_links"),)

    admin_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    admin_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    report_id: Mapped[int | None] = mapped_column(ForeignKey("reports.id"))
    week_id: Mapped[int | None] = mapped_column(ForeignKey("weeks.id"))
    letter_id: Mapped[int | None] = mapped_column(ForeignKey("letters.id"))


class DialogState(Base, TimestampMixin):
    """«Ждём от человека...» state; expires after six hours (people.get_dialog_state)."""

    __tablename__ = "dialog_states"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True, autoincrement=False)
    state: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    updated_at: Mapped[datetime] = mapped_column(
        Timestamp, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Setting(Base, TimestampMixin):
    """Key-value switches, e.g. `reminders_enabled`."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


class ReminderLog(Base, TimestampMixin):
    """Deduplication of reminders: one row per `YYYY-MM-DD:<slug>`."""

    __tablename__ = "reminder_log"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now(), nullable=False)
    recipients: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class AuditLog(Base, TimestampMixin):
    """Every admin content edit, with the row before and after."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Job(Base, TimestampMixin):
    """Background work claimed by the worker with `FOR UPDATE SKIP LOCKED`."""

    __tablename__ = "jobs"
    __table_args__ = (
        enum_check("status", JobStatus, "status"),
        Index("ix_jobs_status_run_after", "status", "run_after"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=JobStatus.QUEUED.value)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    run_after: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(Timestamp)
    finished_at: Mapped[datetime | None] = mapped_column(Timestamp)
    error: Mapped[str | None] = mapped_column(Text)
