"""Data behind the dynamics page: the analyte picker and the chart series.

Pure query and shaping layer without FastAPI. Every query is filtered by the
owner of the sets, and only numeric results are used.
"""

from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from med_analyzer.log import get_logger
from med_analyzer.models import AnalysisSet, Analyte, Result, User
from med_analyzer.norm import is_out_of_range, percent_of_norm

logger = get_logger(__name__)

MAX_ANALYTES = 300
LIKE_ESCAPE = '\\'


def escape_like(text: str) -> str:
    """Escape the wildcards of ``LIKE`` so that ``text`` matches literally.

    Args:
        text: Text typed by the user.

    Returns:
        The text with ``\\``, ``%`` and ``_`` prefixed by a backslash.
    """
    return (
        text.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace('%', LIKE_ESCAPE + '%')
        .replace('_', LIKE_ESCAPE + '_')
    )


def list_analytes(db: Session, user: User, q: str | None) -> list[dict[str, Any]]:
    """List the analytes the user has numeric results for.

    Args:
        db: Open session.
        user: The owner of the results.
        q: Case-insensitive substring of the name or the code; empty or
            ``None`` means no filter.

    Returns:
        Dictionaries with ``id``, ``code``, ``name`` and ``count`` (the number
        of numeric results), ordered by name, at most ``MAX_ANALYTES``.
    """
    count = func.count(Result.id)
    query = (
        select(Analyte.id, Analyte.code, Analyte.name, count)
        .join(Result, Result.analyte_id == Analyte.id)
        .join(AnalysisSet, AnalysisSet.id == Result.set_id)
        .where(AnalysisSet.user_id == user.id, Result.value.is_not(None))
        .group_by(Analyte.id)
        .order_by(Analyte.name, Analyte.id)
        .limit(MAX_ANALYTES)
    )
    if q:
        pattern = f'%{escape_like(q)}%'
        query = query.where(
            Analyte.name.ilike(pattern, escape=LIKE_ESCAPE)
            | Analyte.code.ilike(pattern, escape=LIKE_ESCAPE)
        )
    return [
        {'id': id_, 'code': code, 'name': name, 'count': total}
        for id_, code, name, total in db.execute(query)
    ]


def _point(row: Any) -> dict[str, Any]:
    """Shape one result row as a chart point with its own norm position.

    Args:
        row: Row with the columns selected by ``load_rows``.

    Returns:
        The point; ``percent`` and ``out`` use the range of this very result,
        because labs and age groups change the range over time.
    """
    value, low, high = row.value, row.ref_low, row.ref_high
    return {
        'date': row.collected_on.isoformat(),
        'set_id': row.set_id,
        'value': float(value),
        'value_str': format(value, 'f'),
        'unit': row.unit,
        'low': None if low is None else float(low),
        'high': None if high is None else float(high),
        'ref_raw': row.ref_raw,
        'percent': percent_of_norm(value, low, high),
        'out': is_out_of_range(value, low, high),
    }


def load_rows(db: Session, user: User, analyte_ids: list[int]) -> list[Any]:
    """Load the numeric results of the given analytes in one query.

    Args:
        db: Open session.
        user: The owner of the results.
        analyte_ids: Analytes to load.

    Returns:
        Rows ordered by collection date, set id and result id.
    """
    query = (
        select(
            Result.analyte_id,
            Analyte.name,
            Analyte.code,
            Result.set_id,
            AnalysisSet.collected_on,
            Result.value,
            Result.unit,
            Result.ref_raw,
            Result.ref_low,
            Result.ref_high,
        )
        .join(AnalysisSet, AnalysisSet.id == Result.set_id)
        .join(Analyte, Analyte.id == Result.analyte_id)
        .where(
            AnalysisSet.user_id == user.id,
            Result.value.is_not(None),
            Result.analyte_id.in_(analyte_ids),
        )
        .order_by(AnalysisSet.collected_on, Result.set_id, Result.id)
    )
    return list(db.execute(query))


def _delta(current: Any, previous: Any) -> float | None:
    """Return ``current - previous`` computed exactly, if the units agree.

    Args:
        current: Row of the latest result.
        previous: Row of the result before it, if any.

    Returns:
        The change as a float, or ``None`` when there is no previous result or
        the two were measured in different units.
    """
    if previous is None or previous.unit != current.unit:
        return None
    # Decimal, so that 0.3 - 0.1 is 0.2 and not 0.19999999999999998.
    return float(current.value - previous.value)


def _table_row(rows: list[Any], points: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the comparison table row of one analyte.

    Args:
        rows: Result rows of the analyte, oldest first.
        points: Their points, in the same order.

    Returns:
        The latest value with its range and the value before it.
    """
    current, point = rows[-1], points[-1]
    previous = rows[-2] if len(rows) > 1 else None
    before = points[-2] if previous is not None else None
    return {
        'analyte_id': current.analyte_id,
        'name': current.name,
        'code': current.code,
        'current': point['value'],
        'current_str': point['value_str'],
        'unit': current.unit,
        'date': point['date'],
        'set_id': current.set_id,
        'low': point['low'],
        'high': point['high'],
        'ref_raw': current.ref_raw,
        'out': point['out'],
        'previous': None if before is None else before['value'],
        'previous_str': None if before is None else before['value_str'],
        'previous_date': None if before is None else before['date'],
        'delta': _delta(current, previous),
    }


def build_dynamics(
    db: Session, user: User, analyte_ids: list[int]
) -> dict[str, list[dict[str, Any]]]:
    """Build the chart series and the comparison table.

    Unknown, foreign and text-only analytes are left out silently, so the
    answer never tells whether such an id exists.

    Args:
        db: Open session.
        user: The owner of the results.
        analyte_ids: Requested analytes; duplicates are collapsed.

    Returns:
        ``{'series': [...], 'table': [...]}`` with one entry per analyte that
        has numeric results, in the order of the request.
    """
    wanted = list(dict.fromkeys(analyte_ids))
    if not wanted:
        return {'series': [], 'table': []}
    by_analyte: dict[int, list[Any]] = defaultdict(list)
    for row in load_rows(db, user, wanted):
        by_analyte[row.analyte_id].append(row)
    series = []
    table = []
    for analyte_id in wanted:
        rows = by_analyte.get(analyte_id)
        if not rows:
            continue
        points = [_point(row) for row in rows]
        series.append(
            {
                'analyte_id': analyte_id,
                'name': rows[0].name,
                'code': rows[0].code,
                # The latest unit, since that is what the table row shows.
                'unit': rows[-1].unit,
                'points': points,
            }
        )
        table.append(_table_row(rows, points))
    logger.debug('Dynamics built: analytes=%d', len(series))
    return {'series': series, 'table': table}
