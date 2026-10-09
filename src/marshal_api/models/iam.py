"""Tenancy, plants and users (identity & access)."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from marshal_api.db.session import DialectJSON
from marshal_api.models.base import TenantScopedModel, generate_ulid, utc_now


class Tenant(SQLModel, table=True):
    __tablename__ = "tenants"
    __table_args__ = (UniqueConstraint("slug", name="uq_tenants_slug"),)

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    name: str = Field(max_length=100, nullable=False)
    slug: str = Field(max_length=50, nullable=False)
    settings: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(DialectJSON))
    created_at: datetime = Field(default_factory=utc_now, nullable=False)

    plants: List["Plant"] = Relationship(back_populates="tenant")
    users: List["User"] = Relationship(back_populates="tenant")


class Plant(SQLModel, table=True):
    __tablename__ = "plants"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_tenant_plant_code"),
    )

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    tenant_id: str = Field(foreign_key="tenants.id", max_length=26, nullable=False)
    code: str = Field(max_length=20, nullable=False)
    name: str = Field(max_length=100, nullable=False)
    timezone: str = Field(default="UTC", max_length=50)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)

    tenant: Optional[Tenant] = Relationship(back_populates="plants")
    cars: List["Car"] = Relationship(back_populates="plant")


class User(TenantScopedModel, table=True):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("actor_id", name="uq_users_actor_id"),
        UniqueConstraint("email", name="uq_users_email"),
    )

    id: str = Field(default_factory=generate_ulid, primary_key=True, max_length=26)
    actor_id: str = Field(max_length=50, nullable=False)
    email: str = Field(max_length=255, nullable=False)
    display_name: str = Field(max_length=100, nullable=False)
    role: str = Field(default="supervisor", max_length=50)
    permissions: List[str] = Field(default_factory=list, sa_column=Column(DialectJSON))
    is_active: bool = Field(default=True)

    tenant: Optional[Tenant] = Relationship(back_populates="users")
