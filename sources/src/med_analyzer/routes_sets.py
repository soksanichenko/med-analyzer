"""Pages of the analysis sets: list by date, upload, detail and delete.

Every query is filtered by the current user. A set of another user is
indistinguishable from a missing one (the same plain 404).
"""

from dataclasses import dataclass
from datetime import date
from itertools import groupby
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from med_analyzer.config import AppConfig, get_config
from med_analyzer.db import get_db
from med_analyzer.deps import RequestRefused, current_user, same_origin
from med_analyzer.i18n import Msg, msg
from med_analyzer.log import get_logger
from med_analyzer.models import AnalysisSet, Result, SetFile, User
from med_analyzer.norm import is_out_of_range
from med_analyzer.upload_body import BodyTooLarge, InvalidForm, read_upload
from med_analyzer.uploads import (
    PROFILE_REQUIRED,
    UploadRejected,
    delete_set,
    store_upload,
    too_large_reason,
)
from med_analyzer.web import templates

logger = get_logger(__name__)

router = APIRouter()

# The largest value of the integer primary key; anything above is no set.
MAX_SET_ID = 2**31 - 1
NO_FILE = msg('upload.no_file')
SAVE_FAILED = msg('upload.save_failed')
FLAG_DIRECTIONS = ('high', 'low')


@dataclass(frozen=True)
class SetCard:
    """One set in the list.

    Attributes:
        id: Set id.
        collected_on: Date the samples were collected.
        lab: Lab identifier.
        order_number: Lab order number, if any.
        results: Number of results.
        out_of_range: Number of results outside their reference range.
        files: Number of PDF files in the set.
    """

    id: int
    collected_on: date
    lab: str
    order_number: str | None
    results: int
    out_of_range: int
    files: int


@dataclass(frozen=True)
class ResultRow:
    """One result of a set with what the page derives from it.

    Attributes:
        result: The stored result.
        direction: ``high`` or ``low`` when outside the range, else ``None``.
        flag_mismatch: The lab's printed flag disagrees with the calculation.
    """

    result: Result
    direction: str | None
    flag_mismatch: bool


def load_cards(db: Session, user: User) -> list[SetCard]:
    """Load the user's sets with their counts in one query, newest first.

    Args:
        db: Open session.
        user: The owner.

    Returns:
        One card per set, ordered by collection date and id, both descending.
    """
    outside = and_(
        Result.value.is_not(None),
        or_(Result.value < Result.ref_low, Result.value > Result.ref_high),
    )
    results = (
        select(
            Result.set_id,
            func.count().label('total'),
            func.count().filter(outside).label('outside'),
        )
        .group_by(Result.set_id)
        .subquery()
    )
    files = (
        select(SetFile.set_id, func.count().label('total'))
        .group_by(SetFile.set_id)
        .subquery()
    )
    rows = db.execute(
        select(
            AnalysisSet,
            func.coalesce(results.c.total, 0),
            func.coalesce(results.c.outside, 0),
            func.coalesce(files.c.total, 0),
        )
        .outerjoin(results, results.c.set_id == AnalysisSet.id)
        .outerjoin(files, files.c.set_id == AnalysisSet.id)
        .where(AnalysisSet.user_id == user.id)
        .order_by(AnalysisSet.collected_on.desc(), AnalysisSet.id.desc())
    )
    return [
        SetCard(
            s.id,
            s.collected_on,
            s.lab,
            s.order_number,
            total,
            outside_count,
            file_count,
        )
        for s, total, outside_count, file_count in rows
    ]


def build_rows(results: list[Result]) -> list[ResultRow]:
    """Work out the out-of-range direction and flag check of each result.

    Args:
        results: Results of one set in display order.

    Returns:
        One row per result.
    """
    rows = []
    for result in results:
        outside = is_out_of_range(result.value, result.ref_low, result.ref_high)
        direction = None
        if outside:
            below = result.ref_low is not None and result.value < result.ref_low
            direction = 'low' if below else 'high'
        # Without a computed range there is nothing to compare the flag with.
        printed = result.flag if result.flag in FLAG_DIRECTIONS else None
        mismatch = outside is not None and printed is not None and printed != direction
        rows.append(ResultRow(result, direction, mismatch))
    return rows


def render_sets(
    request: Request,
    db: Session,
    user: User,
    error: Msg | None = None,
    existing_set_id: int | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Render the list page, optionally with an upload error.

    Args:
        request: The incoming request.
        db: Open session.
        user: The caller.
        error: Message shown above the upload form.
        existing_set_id: Set to link to from the message.
        status_code: HTTP status of the response.

    Returns:
        The rendered page.
    """
    cards = load_cards(db, user)
    context = {
        'active': 'sets',
        'profile_complete': user.sex is not None and user.birth_date is not None,
        'groups': [
            (day, list(group))
            for day, group in groupby(cards, key=lambda card: card.collected_on)
        ],
        'has_sets': bool(cards),
        'error': error,
        'existing_set_id': existing_set_id,
    }
    return templates.TemplateResponse(
        request, 'sets.html', context, status_code=status_code
    )


def get_own_set(db: Session, user: User, set_id: int) -> AnalysisSet:
    """Return the user's set or refuse with the 404 of a missing set.

    Args:
        db: Open session.
        user: The caller.
        set_id: Requested id.

    Returns:
        The set.

    Raises:
        RequestRefused: No such set for this user (404).
    """
    analysis_set = None
    if 0 < set_id <= MAX_SET_ID:
        analysis_set = db.scalar(
            select(AnalysisSet).where(
                AnalysisSet.id == set_id, AnalysisSet.user_id == user.id
            )
        )
    if analysis_set is None:
        raise RequestRefused(404, 'Not Found')
    return analysis_set


@router.get('/')
def root() -> RedirectResponse:
    """Send the start page to the list of sets."""
    return RedirectResponse('/sets', status_code=303)


@router.get('/sets', response_class=HTMLResponse)
def list_sets(
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    """List the user's sets grouped by collection date, newest first."""
    return render_sets(request, db, user)


@router.post(
    '/sets/upload', response_class=HTMLResponse, dependencies=[Depends(same_origin)]
)
async def upload_set(
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
    config: Annotated[AppConfig, Depends(get_config)],
) -> Response:
    """Store an uploaded PDF and show the set that holds its results."""
    if user.sex is None or user.birth_date is None:
        return render_sets(request, db, user, PROFILE_REQUIRED, status_code=422)
    try:
        data = await read_upload(request, config)
    except BodyTooLarge:
        return render_sets(request, db, user, too_large_reason(config), status_code=422)
    except InvalidForm:
        return render_sets(request, db, user, NO_FILE, status_code=422)
    try:
        analysis_set, merged = await run_in_threadpool(
            store_upload, db, user, data, config
        )
    except UploadRejected as rejected:
        status = 409 if rejected.existing_set_id is not None else 422
        return render_sets(
            request, db, user, rejected.reason, rejected.existing_set_id, status
        )
    except IntegrityError:
        # Two uploads of one order raced for the same set; the loser may retry.
        db.rollback()
        logger.warning('Upload lost a race on a unique constraint')
        return render_sets(request, db, user, SAVE_FAILED, status_code=409)
    suffix = '?merged=1' if merged else ''
    return RedirectResponse(f'/sets/{analysis_set.id}{suffix}', status_code=303)


@router.get('/sets/{set_id:int}', response_class=HTMLResponse)
def set_detail(
    request: Request,
    set_id: int,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
    merged: str | None = None,
) -> HTMLResponse:
    """Show one set: its results, warnings and files."""
    analysis_set = get_own_set(db, user, set_id)
    results = db.scalars(
        select(Result).where(Result.set_id == set_id).order_by(Result.id)
    ).all()
    context = {
        'active': 'sets',
        'set': analysis_set,
        'rows': build_rows(list(results)),
        'file_count': len(analysis_set.files),
        # Never echoed: any other value just means no notice.
        'merged': merged == '1',
    }
    return templates.TemplateResponse(request, 'set_detail.html', context)


@router.post('/sets/{set_id:int}/delete', dependencies=[Depends(same_origin)])
def delete_set_page(
    set_id: int,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
    config: Annotated[AppConfig, Depends(get_config)],
) -> RedirectResponse:
    """Delete a set with its results and files."""
    deleted = 0 < set_id <= MAX_SET_ID and delete_set(db, user, set_id, config)
    if not deleted:
        raise RequestRefused(404, 'Not Found')
    return RedirectResponse('/sets', status_code=303)
