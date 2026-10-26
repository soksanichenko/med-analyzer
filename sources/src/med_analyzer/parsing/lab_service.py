"""Parser for Lab Service reports (word coordinates in, ``ParsedSet`` out).

The report is a table whose columns are only visible through word positions:
name, result (with the out-of-range glyph right after the value), unit and
reference. The x-ranges of the columns are read from the column-header row of
every table, never hard-coded, because the header row repeats on each page
and in each section.

A long name wraps over several lines placed above and below the line that
carries the result, so a name-only line belongs to the result line closest to
it. Being wrong is worse than being strict: anything with data in the result,
unit or reference columns that cannot be assigned to a name and a single
result raises ``ParseError`` (naming the row and page, never printed values).
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from med_analyzer.log import get_logger
from med_analyzer.parsing.merge import merge_duplicates
from med_analyzer.parsing.primitives import (
    extract_abbreviation,
    normalize_unit,
    parse_decimal,
    parse_ua_date,
)
from med_analyzer.parsing.reference import parse_reference
from med_analyzer.parsing.types import (
    Page,
    ParsedResult,
    ParsedSet,
    ParseError,
    Word,
)

logger = get_logger(__name__)

LAB = 'lab_service'

_HEADER_TEXT = 'Найменування досліджень Результат Од. вимір. Референтні значення'
_TITLE_RE = re.compile(r'Результати досліджень № (\d+)')
_FLAGS = {'▲': 'high', '▼': 'low', '!': 'attention'}

# Words of one visual line differ slightly in ``top`` (mixed font sizes), but
# the nearest distinct lines are about 4.8 pt apart (a wrapped name above or
# below its result line).
_LINE_TOLERANCE = 2.0
# The patient name is printed a hair above its ``ПІБ`` label.
_LABEL_ROW_TOLERANCE = 3.0
# A name-only line within this distance of a result line is part of its name.
_WRAP_TOLERANCE = 6.0
# Slack when deciding that a word crosses a column boundary.
_EDGE_TOLERANCE = 0.5

_NAME, _RESULT, _UNIT, _REFERENCE = range(4)


@dataclass(frozen=True)
class _PageHeader:
    order_number: str
    collected_on: date
    patient_name: str


@dataclass
class _Row:
    """Cells of one table row; name lines carry their ``top`` for ordering."""

    name_lines: list[tuple[float, list[Word]]]
    result: list[Word]
    unit: list[Word]
    reference_lines: list[list[Word]]
    top: float


@dataclass(frozen=True)
class _Columns:
    """Left edges of the result, unit and reference columns."""

    result_x: float
    unit_x: float
    reference_x: float

    def column_of(self, word: Word) -> int | None:
        """Return the column of a word, or ``None`` if it is ambiguous.

        A word centred in the name column is a name even when it overflows a
        little into the next column (section titles do). A word centred
        further right that crosses a column edge cannot be placed.
        """
        centre = (word.x0 + word.x1) / 2
        if centre < self.result_x:
            return _NAME
        for boundary in (self.result_x, self.unit_x, self.reference_x):
            if (
                word.x0 < boundary - _EDGE_TOLERANCE
                and word.x1 > boundary + _EDGE_TOLERANCE
            ):
                return None
        if centre < self.unit_x:
            return _RESULT
        if centre < self.reference_x:
            return _UNIT
        return _REFERENCE


def parse_lab_service(pages: list[Page]) -> ParsedSet:
    """Parse a Lab Service report.

    Args:
        pages: Words of every page, as returned by ``pdf_to_pages``.

    Returns:
        The parsed set. Rows repeated in several sections (WBC, RBC, HGB,
        PLT) are merged, the first occurrence wins; a repeat whose value
        differs adds a warning.

    Raises:
        ParseError: If a page lacks the title, the date, the patient row or a
            column-header row, if the page headers disagree, or if a table
            row cannot be assigned to a name, unit and single result.
    """
    headers = [_parse_header(page, number) for number, page in enumerate(pages, 1)]
    if not headers:
        raise ParseError('Lab Service report has no pages')
    for number, header in enumerate(headers[1:], 2):
        if header != headers[0]:
            raise ParseError(f'Report header on page {number} differs from page 1')

    rows: list[ParsedResult] = []
    for number, page in enumerate(pages, 1):
        rows.extend(_parse_table(page, number))

    results, warnings = merge_duplicates(rows)
    logger.debug('Parsed %d rows into %d results', len(rows), len(results))
    return ParsedSet(
        lab=LAB,
        collected_on=headers[0].collected_on,
        order_number=headers[0].order_number,
        patient_name=headers[0].patient_name,
        results=results,
        warnings=warnings,
    )


def _group_lines(words: list[Word]) -> list[list[Word]]:
    """Group words into visual lines (top to bottom), each sorted by x."""
    lines: list[list[Word]] = []
    first_top = 0.0
    for word in sorted(words, key=lambda w: (w.top, w.x0)):
        if lines and word.top - first_top <= _LINE_TOLERANCE:
            lines[-1].append(word)
        else:
            lines.append([word])
            first_top = word.top
    return [sorted(line, key=lambda w: w.x0) for line in lines]


def _line_text(line: list[Word]) -> str:
    return ' '.join(word.text for word in line)


def _line_top(line: list[Word]) -> float:
    return min(word.top for word in line)


def _parse_header(page: Page, number: int) -> _PageHeader:
    """Read the order number, date and patient name from a page header."""
    lines = _group_lines(page)
    titles = [
        (line, match)
        for line in lines
        if (match := _TITLE_RE.fullmatch(_line_text(line)))
    ]
    if len(titles) != 1:
        raise ParseError(f'Title row not found exactly once on page {number}')
    title_line, title_match = titles[0]

    labels = [word for word in page if word.text == 'ПІБ']
    if len(labels) != 1:
        raise ParseError(f'Patient row not found exactly once on page {number}')
    label = labels[0]
    age_labels = [
        word
        for word in page
        if word.text == 'Вік' and abs(word.top - label.top) <= _LABEL_ROW_TOLERANCE
    ]
    if len(age_labels) != 1:
        raise ParseError(f'Age label next to the patient name missing on page {number}')
    name_words = sorted(
        (
            word
            for word in page
            if abs(word.top - label.top) <= _LABEL_ROW_TOLERANCE
            and word.x0 >= label.x1 - _EDGE_TOLERANCE
            and word.x1 <= age_labels[0].x0 + _EDGE_TOLERANCE
        ),
        key=lambda w: w.x0,
    )
    if not name_words:
        raise ParseError(f'Patient name missing on page {number}')

    dates = set()
    for line in lines:
        if _line_top(title_line) < _line_top(line) < label.top - _LABEL_ROW_TOLERANCE:
            try:
                dates.add(parse_ua_date(_line_text(line)))
            except ValueError:
                continue
    if len(dates) != 1:
        raise ParseError(f'Date row not found exactly once on page {number}')

    return _PageHeader(
        order_number=title_match.group(1),
        collected_on=dates.pop(),
        patient_name=_line_text(name_words),
    )


def _is_footer(line: list[Word]) -> bool:
    """Tell the glyph legend and the page footer apart from table rows."""
    first = line[0].text
    if first == 'Стор.':
        return True
    if first == 'Дата' and len(line) > 1 and line[1].text == 'друку:':
        return True
    return first in _FLAGS and len(line) > 1 and line[1].text == '-'


def _parse_table(page: Page, number: int) -> list[ParsedResult]:
    """Extract the result rows of every table on a page."""
    lines = _group_lines(page)
    header_indexes = [
        i for i, line in enumerate(lines) if _line_text(line) == _HEADER_TEXT
    ]
    if not header_indexes:
        raise ParseError(f'No column header row found on page {number}')

    # Everything from the glyph legend or page footer down is not a table row.
    end = next(
        (i for i in range(header_indexes[0] + 1, len(lines)) if _is_footer(lines[i])),
        len(lines),
    )

    results: list[ParsedResult] = []
    sections = [i for i in header_indexes if i < end]
    for start, stop in zip(sections, [*sections[1:], end]):
        header = {word.text: word.x0 for word in lines[start]}
        columns = _Columns(
            result_x=header['Результат'],
            unit_x=header['Од.'],
            reference_x=header['Референтні'],
        )
        rows = _split_rows(lines[start + 1 : stop], columns, len(results), number)
        for offset, row in enumerate(rows, len(results) + 1):
            results.append(_build_result(row, offset, number))
    return results


def _split_rows(
    lines: list[list[Word]], columns: _Columns, done: int, page_number: int
) -> list[_Row]:
    """Cut the lines of one table into rows, joining wrapped name lines.

    Args:
        lines: Visual lines between a column-header row and the next one.
        columns: Column edges of this table.
        done: Number of rows already read from earlier tables of the page.
        page_number: One-based page number, for error messages.

    Returns:
        One row per line that has anything in the result or unit column,
        top to bottom; reference-only lines below it extend its reference.
    """
    rows: list[_Row] = []
    last_top = 0.0
    text_lines: list[tuple[float, list[Word]]] = []
    for line in lines:
        cells: list[list[Word]] = [[], [], [], []]
        for word in line:
            column = columns.column_of(word)
            if column is None:
                raise ParseError(
                    f'Row {done + len(rows) + 1} on page {page_number}: '
                    'a word crosses a column boundary'
                )
            cells[column].append(word)
        top = _line_top(line)
        if cells[_RESULT] or cells[_UNIT]:
            rows.append(
                _Row(
                    name_lines=[(top, cells[_NAME])] if cells[_NAME] else [],
                    result=cells[_RESULT],
                    unit=cells[_UNIT],
                    reference_lines=[cells[_REFERENCE]] if cells[_REFERENCE] else [],
                    top=top,
                )
            )
            last_top = top
            continue
        if cells[_NAME]:
            text_lines.append((top, cells[_NAME]))
        if cells[_REFERENCE]:
            # A reference that wraps continues below the line of its result.
            if not rows or top - last_top > _WRAP_TOLERANCE:
                raise ParseError(f'Page {page_number}: reference text without a row')
            rows[-1].reference_lines.append(cells[_REFERENCE])
            last_top = top

    attached: list[float] = []
    for top, words in text_lines:
        near = [r for r in rows if abs(r.top - top) <= _WRAP_TOLERANCE]
        if len(near) > 1:
            raise ParseError(
                f'Row {done + rows.index(near[0]) + 1} on page {page_number}: '
                'a wrapped name line fits two rows'
            )
        if near:
            near[0].name_lines.append((top, words))
            attached.append(top)
    # A name line that touches an attached one but not its row would be a
    # name wrapped further than supported; dropping it would cut the name.
    for top, _ in text_lines:
        if top not in attached and any(
            abs(top - other) <= _WRAP_TOLERANCE for other in attached
        ):
            raise ParseError(
                f'Page {page_number}: a name wraps over more lines than supported'
            )
    return rows


def _build_result(row: _Row, index: int, page_number: int) -> ParsedResult:
    """Turn the cells of one row into a ``ParsedResult``."""
    where = f'Row {index} on page {page_number}'
    name = ' '.join(word.text for _, words in sorted(row.name_lines) for word in words)
    if not name:
        raise ParseError(f'{where}: no name found for the row')
    if not row.result:
        raise ParseError(f'{where}: unit or reference without a result')

    for word in (*row.unit, *(w for line in row.reference_lines for w in line)):
        if any(glyph in word.text for glyph in _FLAGS):
            raise ParseError(f'{where}: flag glyph outside the result column')

    flag: str | None = None
    value_words: list[str] = []
    for position, word in enumerate(row.result):
        if word.text in _FLAGS:
            if flag is not None or position != len(row.result) - 1 or not value_words:
                raise ParseError(f'{where}: unexpected flag glyph in the result')
            flag = _FLAGS[word.text]
        elif any(glyph in word.text for glyph in _FLAGS):
            raise ParseError(f'{where}: flag glyph glued to the result')
        else:
            value_words.append(word.text)
    value_text = ' '.join(value_words)
    if not value_text:
        raise ParseError(f'{where}: empty result')
    try:
        value: Decimal | None = parse_decimal(value_text)
        text_value: str | None = None
    except ValueError:
        value, text_value = None, value_text

    unit_text = ' '.join(word.text for word in row.unit)
    return ParsedResult(
        raw_name=name,
        abbreviation=extract_abbreviation(name),
        value=value,
        value_text=text_value,
        unit=normalize_unit(unit_text) if unit_text else None,
        flag=flag,
        reference=parse_reference(
            '\n'.join(
                ' '.join(word.text for word in line) for line in row.reference_lines
            )
        ),
    )
