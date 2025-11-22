"""SQLAlchemy models representing Caddy configuration."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text())

    routes: Mapped[list[Route]] = relationship(back_populates="site", cascade="all, delete-orphan")
    tls_config: Mapped[TLSConfig | None] = relationship(back_populates="site", uselist=False, cascade="all, delete-orphan")
    security_settings: Mapped[SecuritySetting | None] = relationship(back_populates="site", uselist=False, cascade="all, delete-orphan")


class Route(Base):
    __tablename__ = "routes"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    matcher_host: Mapped[str | None] = mapped_column(String(255))
    matcher_path: Mapped[str | None] = mapped_column(String(255))
    matcher_methods: Mapped[str | None] = mapped_column(String(255))
    handler_type: Mapped[str] = mapped_column(String(64), default="reverse_proxy")

    site: Mapped[Site] = relationship(back_populates="routes")
    reverse_proxy: Mapped[ReverseProxySetting | None] = relationship(back_populates="route", uselist=False, cascade="all, delete-orphan")
    caching_policy: Mapped[CachingPolicy | None] = relationship(back_populates="route", uselist=False, cascade="all, delete-orphan")


class ReverseProxySetting(Base):
    __tablename__ = "reverse_proxy_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id", ondelete="CASCADE"), unique=True)
    lb_policy: Mapped[str | None] = mapped_column(String(64))
    health_uri: Mapped[str | None] = mapped_column(String(255))
    max_conns: Mapped[int | None] = mapped_column(Integer)
    fail_timeout: Mapped[str | None] = mapped_column(String(32))
    websocket: Mapped[bool] = mapped_column(Boolean, default=False)
    preserve_host: Mapped[bool] = mapped_column(Boolean, default=True)

    route: Mapped[Route] = relationship(back_populates="reverse_proxy")
    upstreams: Mapped[list[Upstream]] = relationship(back_populates="reverse_proxy", cascade="all, delete-orphan")


class Upstream(Base):
    __tablename__ = "upstreams"

    id: Mapped[int] = mapped_column(primary_key=True)
    reverse_proxy_id: Mapped[int] = mapped_column(ForeignKey("reverse_proxy_settings.id", ondelete="CASCADE"))
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    weight: Mapped[int | None] = mapped_column(Integer)
    backup: Mapped[bool] = mapped_column(Boolean, default=False)

    reverse_proxy: Mapped[ReverseProxySetting] = relationship(back_populates="upstreams")


class TLSConfig(Base):
    __tablename__ = "tls_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), unique=True)
    mode: Mapped[str] = mapped_column(String(32), default="auto")
    email: Mapped[str | None] = mapped_column(String(255))
    cert_file: Mapped[str | None] = mapped_column(String(255))
    key_file: Mapped[str | None] = mapped_column(String(255))
    on_demand_throttle: Mapped[str | None] = mapped_column(Text())

    site: Mapped[Site] = relationship(back_populates="tls_config")


class SecuritySetting(Base):
    __tablename__ = "security_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), unique=True)
    redirect_http_to_https: Mapped[bool] = mapped_column(Boolean, default=True)
    hsts_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    basic_auth_json: Mapped[str | None] = mapped_column(Text())
    ip_allowlist_json: Mapped[str | None] = mapped_column(Text())
    cors_json: Mapped[str | None] = mapped_column(Text())
    headers_json: Mapped[str | None] = mapped_column(Text())

    site: Mapped[Site] = relationship(back_populates="security_settings")


class CachingPolicy(Base):
    __tablename__ = "caching_policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id", ondelete="CASCADE"), unique=True)
    type: Mapped[str] = mapped_column(String(32), default="none")
    config_json: Mapped[str | None] = mapped_column(Text())

    route: Mapped[Route] = relationship(back_populates="caching_policy")


class Meta(Base):
    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def to_dict(instance: Base) -> dict[str, Any]:
    """Return a dictionary of column values for debugging."""
    return {column.key: getattr(instance, column.key) for column in instance.__table__.columns}
