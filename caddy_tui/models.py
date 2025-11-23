"""SQLAlchemy models for logical Caddyfile storage."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


SNAPSHOT_KIND_CADDY_TUI = "caddy_tui"
SNAPSHOT_KIND_CADDYFILE = "caddyfile"
SNAPSHOT_KIND_CADDY_LIVE = "caddy_live"
SnapshotKind = Literal[
    SNAPSHOT_KIND_CADDY_TUI,
    SNAPSHOT_KIND_CADDYFILE,
    SNAPSHOT_KIND_CADDY_LIVE,
]


class Config(Base):
    __tablename__ = "configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    caddyfile_path: Mapped[str] = mapped_column(Text(), nullable=False)
    last_imported_at: Mapped[str | None] = mapped_column(String(64))
    last_exported_at: Mapped[str | None] = mapped_column(String(64))
    last_caddyfile_hash: Mapped[str | None] = mapped_column(String(128))

    snapshots: Mapped[list[ConfigSnapshot]] = relationship(
        back_populates="config",
        cascade="all, delete-orphan",
        order_by="ConfigSnapshot.id",
    )


class ConfigSnapshot(Base):
    __tablename__ = "config_snapshots"
    __table_args__ = (UniqueConstraint("config_id", "source_kind", name="uq_config_snapshot_kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("configs.id", ondelete="CASCADE"), index=True)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_label: Mapped[str | None] = mapped_column(String(64))
    source_path: Mapped[str | None] = mapped_column(Text())
    source_hash: Mapped[str | None] = mapped_column(String(128))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    config: Mapped[Config] = relationship(back_populates="snapshots")
    server_blocks: Mapped[list[ServerBlock]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
        order_by="ServerBlock.block_index",
    )


class ServerBlock(Base):
    __tablename__ = "server_blocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("config_snapshots.id", ondelete="CASCADE"), index=True)
    block_index: Mapped[int] = mapped_column(Integer, nullable=False)
    is_global: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_prelude: Mapped[str | None] = mapped_column(Text())
    raw_postlude: Mapped[str | None] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    snapshot: Mapped[ConfigSnapshot] = relationship(back_populates="server_blocks")
    sites: Mapped[list[ServerBlockSite]] = relationship(
        back_populates="block",
        cascade="all, delete-orphan",
        order_by="ServerBlockSite.label_index",
    )
    directives: Mapped[list[Directive]] = relationship(
        back_populates="block",
        cascade="all, delete-orphan",
        order_by="Directive.line_index",
    )
    fragments: Mapped[list[RawFragment]] = relationship(
        back_populates="block",
        cascade="all, delete-orphan",
        order_by="RawFragment.fragment_index",
    )


class ServerBlockSite(Base):
    __tablename__ = "server_block_sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    block_id: Mapped[int] = mapped_column(ForeignKey("server_blocks.id", ondelete="CASCADE"), index=True)
    raw_label: Mapped[str] = mapped_column(Text(), nullable=False)
    host: Mapped[str | None] = mapped_column(String(255))
    port: Mapped[int | None] = mapped_column(Integer)
    scheme: Mapped[str | None] = mapped_column(String(16))
    is_ipv6: Mapped[bool] = mapped_column(Boolean, default=False)
    is_wildcard: Mapped[bool] = mapped_column(Boolean, default=False)
    label_index: Mapped[int] = mapped_column(Integer, nullable=False)

    block: Mapped[ServerBlock] = relationship(back_populates="sites")


class Directive(Base):
    __tablename__ = "directives"

    id: Mapped[int] = mapped_column(primary_key=True)
    block_id: Mapped[int] = mapped_column(ForeignKey("server_blocks.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    matcher: Mapped[str | None] = mapped_column(String(128))
    line_index: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_leading: Mapped[str | None] = mapped_column(Text())
    raw_trailing: Mapped[str | None] = mapped_column(Text())
    has_block: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_block_body: Mapped[str | None] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    block: Mapped[ServerBlock] = relationship(back_populates="directives")
    args: Mapped[list[DirectiveArg]] = relationship(
        back_populates="directive",
        cascade="all, delete-orphan",
        order_by="DirectiveArg.arg_index",
    )
    kv_pairs: Mapped[list[DirectiveKeyValue]] = relationship(
        back_populates="directive",
        cascade="all, delete-orphan",
        order_by="DirectiveKeyValue.kv_index",
    )


class DirectiveArg(Base):
    __tablename__ = "directive_args"

    id: Mapped[int] = mapped_column(primary_key=True)
    directive_id: Mapped[int] = mapped_column(ForeignKey("directives.id", ondelete="CASCADE"), index=True)
    arg_index: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[str] = mapped_column(Text(), nullable=False)

    directive: Mapped[Directive] = relationship(back_populates="args")


class DirectiveKeyValue(Base):
    __tablename__ = "directive_kv"

    id: Mapped[int] = mapped_column(primary_key=True)
    directive_id: Mapped[int] = mapped_column(ForeignKey("directives.id", ondelete="CASCADE"), index=True)
    section: Mapped[str | None] = mapped_column(String(64))
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[str] = mapped_column(Text(), nullable=False)
    kv_index: Mapped[int] = mapped_column(Integer, nullable=False)

    directive: Mapped[Directive] = relationship(back_populates="kv_pairs")


class RawFragment(Base):
    __tablename__ = "raw_fragments"

    id: Mapped[int] = mapped_column(primary_key=True)
    block_id: Mapped[int] = mapped_column(ForeignKey("server_blocks.id", ondelete="CASCADE"), index=True)
    fragment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text(), nullable=False)

    block: Mapped[ServerBlock] = relationship(back_populates="fragments")


class Meta(Base):
    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


def to_dict(instance: Base) -> dict[str, Any]:
    """Return a dictionary of column values for debugging."""
    return {column.key: getattr(instance, column.key) for column in instance.__table__.columns}
