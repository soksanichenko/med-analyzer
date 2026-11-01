"""The dynamics page and the JSON behind it.

All routes are read-only GETs and answer only with the caller's own data.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import Field
from sqlalchemy.orm import Session

from med_analyzer.db import get_db
from med_analyzer.deps import current_user
from med_analyzer.dynamics import build_dynamics, list_analytes
from med_analyzer.i18n import detect_language
from med_analyzer.models import User
from med_analyzer.web import templates

router = APIRouter()

# The largest value of the integer primary key; anything above is no analyte.
MAX_ID = 2**31 - 1
MAX_SELECTED = 12
MAX_SEARCH_LENGTH = 100

AnalyteId = Annotated[int, Field(gt=0, le=MAX_ID)]


@router.get('/dynamics', response_class=HTMLResponse)
def dynamics_page(
    request: Request, user: Annotated[User, Depends(current_user)]
) -> HTMLResponse:
    """Show the analyte picker, the chart and the comparison table."""
    context = {'active': 'dynamics'}
    return templates.TemplateResponse(request, 'dynamics.html', context)


@router.get('/api/analytes')
def analytes(
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
    q: Annotated[str | None, Query(max_length=MAX_SEARCH_LENGTH)] = None,
) -> list[dict[str, Any]]:
    """List the analytes the user has numeric results for, filtered by ``q``."""
    return list_analytes(db, user, q, detect_language(request))


@router.get('/api/dynamics')
def dynamics(
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
    analyte_id: Annotated[
        list[AnalyteId] | None, Query(max_length=MAX_SELECTED)
    ] = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return the series and the table of the chosen analytes."""
    return build_dynamics(db, user, analyte_id or [], detect_language(request))
