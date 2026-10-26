"""SQLAlchemy models."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base with deterministic constraint names for Alembic."""

    metadata = MetaData(
        naming_convention={
            'ix': 'ix_%(column_0_label)s',
            'uq': 'uq_%(table_name)s_%(column_0_N_name)s',
            'ck': 'ck_%(table_name)s_%(constraint_name)s',
            'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
            'pk': 'pk_%(table_name)s',
        }
    )


class User(Base):
    """A person identified by a Discord account."""

    __tablename__ = 'users'
    __table_args__ = (CheckConstraint("sex IN ('M', 'F')", name='sex'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    discord_user_id: Mapped[str] = mapped_column(String(64), unique=True)
    sex: Mapped[str | None] = mapped_column(String(1))
    birth_date: Mapped[date | None] = mapped_column(Date)
    last_patient_name: Mapped[str | None] = mapped_column(String(255))


class Analyte(Base):
    """A measured quantity, e.g. hemoglobin."""

    __tablename__ = 'analytes'

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))


class AnalyteAlias(Base):
    """A spelling of an analyte name as printed by a lab."""

    __tablename__ = 'analyte_aliases'

    id: Mapped[int] = mapped_column(primary_key=True)
    analyte_id: Mapped[int] = mapped_column(ForeignKey('analytes.id'), index=True)
    alias: Mapped[str] = mapped_column(String(255), unique=True)


class AnalysisSet(Base):
    """Results of one lab order; several PDF files may belong to it."""

    __tablename__ = 'analysis_sets'
    __table_args__ = (
        CheckConstraint("lab IN ('lab_service', 'synevo')", name='lab'),
        # The merge key. Orders without a number never collide with each other.
        Index(
            'uq_analysis_sets_user_lab_order',
            'user_id',
            'lab',
            'order_number',
            unique=True,
            postgresql_where=text('order_number IS NOT NULL'),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    lab: Mapped[str] = mapped_column(String(32))
    collected_on: Mapped[date] = mapped_column(Date)
    order_number: Mapped[str | None] = mapped_column(String(64))
    patient_name: Mapped[str | None] = mapped_column(String(255))
    warnings: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    results: Mapped[list['Result']] = relationship(
        back_populates='analysis_set',
        cascade='all, delete-orphan',
        passive_deletes=True,
    )
    files: Mapped[list['SetFile']] = relationship(
        back_populates='analysis_set',
        cascade='all, delete-orphan',
        passive_deletes=True,
    )


class SetFile(Base):
    """An uploaded PDF belonging to a set."""

    __tablename__ = 'set_files'
    __table_args__ = (UniqueConstraint('user_id', 'sha256'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    set_id: Mapped[int] = mapped_column(
        ForeignKey('analysis_sets.id', ondelete='CASCADE'), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    sha256: Mapped[str] = mapped_column(String(64))
    file_path: Mapped[str] = mapped_column(String(512))
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    analysis_set: Mapped[AnalysisSet] = relationship(back_populates='files')


class Result(Base):
    """One measured value of an analyte within a set."""

    __tablename__ = 'results'
    __table_args__ = (
        CheckConstraint("flag IN ('high', 'low', 'attention')", name='flag'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    set_id: Mapped[int] = mapped_column(
        ForeignKey('analysis_sets.id', ondelete='CASCADE'), index=True
    )
    analyte_id: Mapped[int] = mapped_column(ForeignKey('analytes.id'), index=True)
    raw_name: Mapped[str] = mapped_column(String(255))
    value: Mapped[Decimal | None] = mapped_column(Numeric)
    # Text, not a bounded string: lab text values and Synevo reference blocks
    # have no fixed maximum length and must never be truncated.
    value_text: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(64))
    flag: Mapped[str | None] = mapped_column(String(16))
    ref_raw: Mapped[str | None] = mapped_column(Text)
    ref_groups: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    ref_low: Mapped[Decimal | None] = mapped_column(Numeric)
    ref_high: Mapped[Decimal | None] = mapped_column(Numeric)

    analysis_set: Mapped[AnalysisSet] = relationship(back_populates='results')
