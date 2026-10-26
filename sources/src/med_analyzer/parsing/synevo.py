"""Parser for Synevo reports (word coordinates in, ``ParsedSet`` out).

Every page repeats the report header (order date, client, birth-date line
ending with the order number) and the column-header row ``Показник /
Результат / Од. / Референтний інтервал``, and ends with a boilerplate footer.
Only the lines between the column-header row and the footer are table lines.

The columns are only visible through word positions. Results and units are
centred under their labels and the reference text is left-aligned, so the
column edges are derived from the label positions of the column-header row.
A line is first cut into runs of closely spaced words: a table cell is a run
inside one column, while a run that spills over a column edge is free text
(a package heading or a method paragraph), never part of a row.

A row starts at a line with both a name and a result. Names, units and text
results wrap downwards onto the next lines; the reference block runs until
the next row or heading. Method/reagent/analyzer lines at the end of the
block and clearly delimited ``*`` footnotes are dropped. Anything that does
not fit this model raises ``ParseError`` naming the page and row, never the
printed values.
"""

import re
from dataclasses import dataclass, field
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

LAB = 'synevo'

_HEADER_TEXT = 'Показник Результат Од. Референтний інтервал'
_FOOTER_TEXT = 'Результати лабораторних досліджень не є достатньою підставою'
# Legend printed in the reference column of the first section heading.
_LEGEND_TEXT = 'Індикатор зони підвищеної уваги'
_METHOD_PREFIXES = ('Метод:', 'Реагенти:', 'Аналізатор:', 'Виконано на')
_PACKAGE = 'Пакет'

_DATE_RE = re.compile(r'\d{2}\.\d{2}\.\d{4}')
_TIME_RE = re.compile(r'\d{1,2}:\d{2}')
_ORDER_RE = re.compile(r'\d{10}')
# Anything a range line could contain; method continuation lines have none.
_REFERENCE_LIKE_RE = re.compile(r':|[<>≤≥]\s*\d|\d\s*[-–]\s*\d')
_BOUND_RE = re.compile(r'[<>≤≥]\s*\d')
_NUM = r'\d+(?:[.,]\d+)?'
# Range data inside a would-be footnote: a bare ``4.5 - 5.5`` line, or a
# value right after a word label (``Чоловіки: 4.0 - 5.0``, ``років): <15``).
# The label must contain a letter, so citations such as
# ``2016;9(5):8169-8175`` or ``Vol 135, 2011`` do not count.
_BARE_RANGE_RE = re.compile(rf'[<>≤≥]?\s*{_NUM}\s*[-–]\s*[<>≤≥]?\s*{_NUM}\**')
_LABELLED_VALUE_RE = re.compile(rf'(?:^|\s)[^\s:]*[^\W\d_][^\s:]*:\s*[<>≤≥]?\s*{_NUM}')

# Words of one visual line differ in ``top`` by up to 1.5 pt; distinct lines
# are at least 9 pt apart.
_LINE_TOLERANCE = 2.0
# Words of one text run are about 2 pt apart; cells of a row are at least
# 14 pt apart (unit to reference).
_RUN_GAP = 6.0
# A wrapped name, unit or text result is printed 11.25-11.9 pt below the
# previous line of its cell; headings are at least 16 pt below a row.
_WRAP_GAP = 13.0
# Slack when deciding that a word crosses a column edge or shares an indent.
_EDGE_TOLERANCE = 0.5
# Units are centred under ``Од.`` and end at most 8 pt right of the label;
# the reference text starts 22 pt right of it. The edge goes in between.
_UNIT_REFERENCE_GAP = 15.0

_NAME, _RESULT, _UNIT, _REFERENCE = range(4)


@dataclass(frozen=True)
class _PageHeader:
    collected_on: date
    patient_name: str
    order_number: str


@dataclass
class _Row:
    """Cells of one table row; cell lines carry their ``top``."""

    name_x: float
    name_lines: list[tuple[float, list[Word]]]
    result_lines: list[tuple[float, list[Word]]]
    unit_lines: list[tuple[float, list[Word]]]
    reference_lines: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Columns:
    """Right edges of the name, result and unit columns."""

    name_end: float
    result_end: float
    unit_end: float

    @classmethod
    def from_header(cls, line: list[Word]) -> '_Columns':
        """Derive the column edges from the column-header row.

        The result field is centred under ``Результат`` and ends halfway to
        ``Од.``; it is as wide on the left of its label as on the right.
        """
        labels = {word.text: word for word in line}
        result, unit = labels['Результат'], labels['Од.']
        result_end = (result.x1 + unit.x0) / 2
        return cls(
            name_end=result.x0 + result.x1 - result_end,
            result_end=result_end,
            unit_end=unit.x1 + _UNIT_REFERENCE_GAP,
        )

    def column_of(self, word: Word) -> int | None:
        """Return the column of a word, or ``None`` if it crosses an edge."""
        edges = (self.name_end, self.result_end, self.unit_end)
        for edge in edges:
            if word.x0 < edge - _EDGE_TOLERANCE and word.x1 > edge + _EDGE_TOLERANCE:
                return None
        centre = (word.x0 + word.x1) / 2
        return sum(centre >= edge for edge in edges)

    def column_of_run(self, run: list[Word]) -> int | None:
        """Return the column of a run, or ``None`` if it spans several."""
        columns = {self.column_of(word) for word in run}
        return columns.pop() if len(columns) == 1 else None


def parse_synevo(pages: list[Page]) -> ParsedSet:
    """Parse a Synevo report.

    Args:
        pages: Words of every page, as returned by ``pdf_to_pages``.

    Returns:
        The parsed set. A repeated analyte keeps its first occurrence; a
        repeat whose value differs adds a warning.

    Raises:
        ParseError: If a page lacks a header field, the column-header row or
            the footer, if the page headers disagree, or if a table line
            cannot be assigned to a row, a heading or a method paragraph.
    """
    headers = [_parse_header(page, number) for number, page in enumerate(pages, 1)]
    if not headers:
        raise ParseError('Synevo report has no pages')
    for number, header in enumerate(headers[1:], 2):
        if header != headers[0]:
            raise ParseError(f'Report header on page {number} differs from page 1')

    rows: list[ParsedResult] = []
    for number, page in enumerate(pages, 1):
        rows.extend(_parse_page(page, number))

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


def _text(words: list[Word]) -> str:
    return ' '.join(word.text for word in words)


def _line_top(line: list[Word]) -> float:
    return min(word.top for word in line)


def _runs(line: list[Word]) -> list[list[Word]]:
    """Cut a line into runs of words separated by ordinary spaces."""
    runs: list[list[Word]] = []
    for word in line:
        if runs and word.x0 - runs[-1][-1].x1 <= _RUN_GAP:
            runs[-1].append(word)
        else:
            runs.append([word])
    return runs


def _after_label(page: Page, text: str, number: int) -> list[Word]:
    """Words right of a header label on its line, left to right."""
    labels = [word for word in page if word.text == text]
    if len(labels) != 1:
        raise ParseError(
            f'Header label {text!r} not found exactly once on page {number}'
        )
    label = labels[0]
    return sorted(
        (
            word
            for word in page
            if abs(word.top - label.top) <= _LINE_TOLERANCE
            and word.x0 >= label.x1 - _EDGE_TOLERANCE
        ),
        key=lambda w: w.x0,
    )


def _parse_header(page: Page, number: int) -> _PageHeader:
    """Read the order date, client name and order number of a page."""
    ordered = _after_label(page, 'замовлення:', number)
    if (
        not ordered
        or not _DATE_RE.fullmatch(ordered[0].text)
        or (len(ordered) > 1 and _TIME_RE.fullmatch(ordered[1].text) is None)
    ):
        raise ParseError(f'Order date not found on page {number}')
    try:
        collected_on = parse_ua_date(ordered[0].text)
    except ValueError as error:
        raise ParseError(f'Order date is not a valid date on page {number}') from error

    name_runs = _runs(_after_label(page, 'Клієнт:', number))
    if len(name_runs) != 1:
        raise ParseError(f'Client name not found as one run on page {number}')

    birth_line = _after_label(page, 'народження:', number)
    numbers = [word for word in birth_line if _ORDER_RE.fullmatch(word.text)]
    if len(numbers) != 1 or numbers[0] is not birth_line[-1]:
        raise ParseError(
            f'Order number not found on the birth-date line of page {number}'
        )

    return _PageHeader(
        collected_on=collected_on,
        patient_name=_text(name_runs[0]),
        order_number=numbers[0].text,
    )


def _parse_page(page: Page, number: int) -> list[ParsedResult]:
    """Extract the result rows between the column header and the footer."""
    lines = _group_lines(page)
    headers = [i for i, line in enumerate(lines) if _text(line) == _HEADER_TEXT]
    if len(headers) != 1:
        raise ParseError(f'No single column header row found on page {number}')
    start = headers[0]
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if _text(lines[i]).startswith(_FOOTER_TEXT)
        ),
        None,
    )
    if end is None:
        raise ParseError(f'Page footer not found on page {number}')

    columns = _Columns.from_header(lines[start])
    rows = _split_rows(lines[start + 1 : end], columns, number)
    return [_build_result(row, index, number) for index, row in enumerate(rows, 1)]


def _split_rows(
    lines: list[list[Word]], columns: _Columns, page_number: int
) -> list[_Row]:
    """Cut the table lines of one page into rows, skipping headings.

    Args:
        lines: Visual lines between the column-header row and the footer.
        columns: Column edges of the page.
        page_number: One-based page number, for error messages.

    Returns:
        The rows, top to bottom, with their wrapped cells joined.
    """
    rows: list[_Row] = []
    current: _Row | None = None
    # Left edge and last ``top`` of a method paragraph being skipped.
    paragraph: tuple[float, float] | None = None
    for line in lines:
        top = _line_top(line)
        where = f'Page {page_number}, after row {len(rows)}'
        if paragraph is not None and _continues_paragraph(line, paragraph, columns):
            paragraph = (paragraph[0], top)
            continue
        paragraph = None

        runs = _runs(line)
        kinds = [columns.column_of_run(run) for run in runs]
        if None in kinds or line[0].text == _PACKAGE:
            # Free text or a package heading: ends the current row. A package
            # heading can overflow into the result column, so it is checked
            # before the cells; it must never become a row.
            paragraph = _heading_paragraph(runs, kinds, columns, where)
            current = None
            continue

        cells: list[list[Word]] = [[], [], [], []]
        for run, kind in zip(runs, kinds, strict=True):
            cells[kind].extend(run)
        name, result, unit, reference = cells

        if name and result:
            current = _Row(
                name_x=name[0].x0,
                name_lines=[(top, name)],
                result_lines=[(top, result)],
                unit_lines=[(top, unit)] if unit else [],
            )
            rows.append(current)
            unit = []
        elif name:
            if (
                current is not None
                and abs(name[0].x0 - current.name_x) <= _EDGE_TOLERANCE
                and top - current.name_lines[-1][0] <= _WRAP_GAP
            ):
                current.name_lines.append((top, name))
            else:
                # Section headings, ``Примітка:`` and similar name-only lines.
                if unit or (reference and _text(reference) != _LEGEND_TEXT):
                    raise ParseError(
                        f'{where}: a heading carries unit or reference text'
                    )
                current = None
                continue
        elif current is None:
            raise ParseError(f'{where}: result, unit or reference text without a row')
        elif result:
            if top - current.result_lines[-1][0] > _WRAP_GAP:
                raise ParseError(
                    f'Row {len(rows)} on page {page_number}: result text far below '
                    'its row'
                )
            current.result_lines.append((top, result))

        if unit:
            if not current.unit_lines or top - current.unit_lines[-1][0] > _WRAP_GAP:
                raise ParseError(
                    f'Row {len(rows)} on page {page_number}: unit text far from its row'
                )
            current.unit_lines.append((top, unit))
        if reference:
            current.reference_lines.append(_text(reference))
    return rows


def _heading_paragraph(
    runs: list[list[Word]],
    kinds: list[int | None],
    columns: _Columns,
    where: str,
) -> tuple[float, float] | None:
    """Check a heading line and return the paragraph it opens, if any.

    Allowed shapes are a heading starting in the name column that runs over
    the column edges (``Пакет …``, ``Загальний аналіз крові (…``), a package
    heading made of name and result cells only, and a name-column label
    followed by a free-text paragraph (``Методи/Лабораторне обладнання: …``).
    """
    first = runs[0][0]
    if columns.column_of(first) != _NAME:
        raise ParseError(f'{where}: text crosses the table columns')
    if None not in kinds:
        if any(kind in (_UNIT, _REFERENCE) for kind in kinds):
            raise ParseError(f'{where}: a heading carries unit or reference text')
        return None
    spanning = kinds.index(None)
    if (
        spanning != len(runs) - 1
        or spanning > 1
        or kinds[:spanning] not in ([], [_NAME])
    ):
        raise ParseError(f'{where}: text crosses the table columns')
    paragraph = runs[spanning][0]
    if spanning == 0:
        return None
    return paragraph.x0, paragraph.top


def _continues_paragraph(
    line: list[Word], paragraph: tuple[float, float], columns: _Columns
) -> bool:
    """Tell whether a line is the next line of a skipped method paragraph."""
    left, last_top = paragraph
    return (
        abs(line[0].x0 - left) <= _EDGE_TOLERANCE
        and _line_top(line) - last_top <= _WRAP_GAP
        and all(columns.column_of(word) != _NAME for word in line)
    )


def _is_number(text: str) -> bool:
    try:
        parse_decimal(text)
    except ValueError:
        return False
    return True


def _clean_reference(lines: list[str], where: str) -> list[str]:
    """Drop the method trailer and clearly delimited footnotes of a block."""
    cut = next(
        (i for i, line in enumerate(lines) if line.startswith(_METHOD_PREFIXES)),
        len(lines),
    )
    for line in lines[cut:]:
        # Method lines wrap (``(ECLIA)``, analyzer lists); a line that could
        # be a range after them means the block is not what we think it is.
        if not line.startswith(_METHOD_PREFIXES) and _REFERENCE_LIKE_RE.search(line):
            raise ParseError(f'{where}: reference text after the method lines')

    kept: list[str] = []
    lines = lines[:cut]
    i = 0
    while i < len(lines):
        line = lines[i]
        # ``*Діти: …`` and ``**Вагітні жінки:`` are starred labels, not notes.
        if line.startswith('*') and ':' not in line:
            end = next(
                (j for j in range(i + 1, len(lines)) if lines[j].startswith('*')),
                len(lines),
            )
            # A footnote runs up to the next starred line or the end; if a
            # label, a bound or range data shows up in that stretch, its end
            # is unclear and everything is kept (``parse_reference`` ignores
            # stray text, while a dropped range would be lost).
            if not any(_looks_like_data(note) for note in lines[i:end]):
                i = end
                continue
        kept.append(line)
        i += 1
    return kept


def _looks_like_data(line: str) -> bool:
    """Tell whether a line may hold a reference label, bound or range."""
    return bool(
        line.endswith(':')
        or _BOUND_RE.search(line)
        or _BARE_RANGE_RE.fullmatch(line)
        or _LABELLED_VALUE_RE.search(line)
    )


def _build_result(row: _Row, index: int, page_number: int) -> ParsedResult:
    """Turn the cells of one row into a ``ParsedResult``."""
    where = f'Row {index} on page {page_number}'
    name = ' '.join(_text(words) for _, words in row.name_lines)
    result_parts = [_text(words) for _, words in row.result_lines]
    # Only a text result may wrap; a number split over lines is not guessed.
    if len(result_parts) > 1 and any(_is_number(part) for part in result_parts):
        raise ParseError(f'{where}: the result wraps over several lines')
    result_text = ' '.join(result_parts)
    if _is_number(result_text):
        value: Decimal | None = parse_decimal(result_text)
        value_text: str | None = None
    else:
        value, value_text = None, result_text

    unit_text = ' '.join(_text(words) for _, words in row.unit_lines)
    reference = '\n'.join(_clean_reference(row.reference_lines, where))
    return ParsedResult(
        raw_name=name,
        abbreviation=extract_abbreviation(name),
        value=value,
        value_text=value_text,
        unit=normalize_unit(unit_text) if unit_text else None,
        flag=None,
        reference=parse_reference(reference),
    )
