"""Database constraints that the upload logic relies on."""

from collections.abc import Callable
from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from med_analyzer.models import AnalysisSet, Analyte, Result, SetFile, User

SHA = 'a' * 64


def _make_set(
    db: Session,
    user: User,
    lab: str = 'lab_service',
    order_number: str | None = None,
) -> AnalysisSet:
    analysis_set = AnalysisSet(
        user_id=user.id,
        lab=lab,
        collected_on=date(2026, 1, 15),
        order_number=order_number,
    )
    db.add(analysis_set)
    db.commit()
    return analysis_set


def _count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model))


def test_file_hash_unique_per_user(db: Session, make_user: Callable[..., User]) -> None:
    first = make_user('u1')
    second = make_user('u2')
    first_set = _make_set(db, first)
    second_set = _make_set(db, second)
    db.add(SetFile(set_id=first_set.id, user_id=first.id, sha256=SHA, file_path='a'))
    db.commit()

    db.add(SetFile(set_id=first_set.id, user_id=first.id, sha256=SHA, file_path='b'))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    db.add(SetFile(set_id=second_set.id, user_id=second.id, sha256=SHA, file_path='c'))
    db.commit()
    assert _count(db, SetFile) == 2


def test_order_number_unique_only_when_present(
    db: Session, make_user: Callable[..., User]
) -> None:
    first = make_user('u1')
    second = make_user('u2')
    _make_set(db, first, order_number=None)
    _make_set(db, first, order_number=None)

    _make_set(db, first, order_number='N-1')
    with pytest.raises(IntegrityError):
        _make_set(db, first, order_number='N-1')
    db.rollback()

    _make_set(db, second, order_number='N-1')
    _make_set(db, first, lab='synevo', order_number='N-1')
    assert _count(db, AnalysisSet) == 5


def test_deleting_set_cascades_results_and_files(
    db: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    analysis_set = _make_set(db, user, order_number='N-1')
    analyte = Analyte(code='HGB', name='Hemoglobin')
    db.add(analyte)
    db.commit()
    db.add(
        Result(
            set_id=analysis_set.id,
            analyte_id=analyte.id,
            raw_name='Hemoglobin',
            value=140,
        )
    )
    db.add(SetFile(set_id=analysis_set.id, user_id=user.id, sha256=SHA, file_path='a'))
    db.commit()

    db.delete(analysis_set)
    db.commit()

    assert _count(db, AnalysisSet) == 0
    assert _count(db, Result) == 0
    assert _count(db, SetFile) == 0
    assert _count(db, Analyte) == 1
