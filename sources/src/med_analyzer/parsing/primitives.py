"""Small pure helpers for parsing numbers, dates, units and names."""

import re
from datetime import date
from decimal import Decimal

# Month names in nominative and genitive forms, as seen in lab reports.
_MONTHS: dict[str, int] = {
    name: number
    for number, names in enumerate(
        (
            ('січень', 'січня'),
            ('лютий', 'лютого'),
            ('березень', 'березня'),
            ('квітень', 'квітня'),
            ('травень', 'травня'),
            ('червень', 'червня'),
            ('липень', 'липня'),
            ('серпень', 'серпня'),
            ('вересень', 'вересня'),
            ('жовтень', 'жовтня'),
            ('листопад', 'листопада'),
            ('грудень', 'грудня'),
        ),
        start=1,
    )
    for name in names
}

_DECIMAL_RE = re.compile(r'[+-]?\d+(?:[.,]\d+)?')
_WORD_DATE_RE = re.compile(r'(\d{1,2})\s+([^\W\d_]+)\s+(\d{4})')
_NUMERIC_DATE_RE = re.compile(r'(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s+\d{1,2}:\d{2})?')
# Synevo prints "10^9 клітин/л" (sometimes broken across lines); the canonical
# form drops the redundant word so units compare equal across labs.
_CELLS_UNIT_RE = re.compile(r'(10\^\d+)\s*/?\s*клітин\s*/\s*л')
_LATIN_TOKEN_RE = re.compile(r'[A-Za-z0-9#%-]+')


def parse_decimal(s: str) -> Decimal:
    """Parse a decimal number written with a comma or a dot.

    Args:
        s: Text such as ``'4,63'`` or ``'0.390'``; spaces and NBSP are ignored.

    Returns:
        The parsed value.

    Raises:
        ValueError: If the text is empty or not a plain decimal number
            (exponent forms, NaN and infinity are rejected too).
    """
    cleaned = re.sub(r'[\s ]+', '', s)
    if not _DECIMAL_RE.fullmatch(cleaned):
        raise ValueError(f'Not a decimal number: {s!r}')
    return Decimal(cleaned.replace(',', '.'))


def parse_ua_date(s: str) -> date:
    """Parse a date as printed in Ukrainian lab reports.

    Accepted forms: ``DD <month> YYYY`` (month in nominative or genitive,
    any case), ``DD.MM.YYYY`` and ``DD.MM.YYYY HH:MM`` (time is ignored).

    Args:
        s: The date text.

    Returns:
        The parsed date.

    Raises:
        ValueError: If the text matches none of the forms or is not a real date.
    """
    text = ' '.join(s.split())
    match = _NUMERIC_DATE_RE.fullmatch(text)
    if match:
        day, month, year = (int(g) for g in match.groups())
        return date(year, month, day)
    match = _WORD_DATE_RE.fullmatch(text)
    if match:
        month = _MONTHS.get(match.group(2).lower())
        if month is None:
            raise ValueError(f'Unknown month name in date: {s!r}')
        return date(int(match.group(3)), month, int(match.group(1)))
    raise ValueError(f'Unrecognised date: {s!r}')


def normalize_unit(s: str) -> str:
    """Normalise a unit string for cross-lab comparison.

    Whitespace is collapsed, trailing dots are dropped (Synevo prints
    ``мм/год.``, Lab Service ``мм/год``) and the Synevo form ``10^9 клітин/л``
    becomes ``10^9/л``; everything else is returned unchanged.

    Args:
        s: The raw unit text.

    Returns:
        The normalised unit.
    """
    text = ' '.join(s.split()).rstrip('. ')
    return _CELLS_UNIT_RE.sub(r'\1/л', text)


def extract_abbreviation(name: str) -> str | None:
    """Extract the Latin abbreviation from an analyte name.

    Looks at the last top-level parenthesised group (nesting is balanced, so
    the inner ``(OH)`` of ``(25-гідроксивітамін D, 25-(OH)D)`` is not a
    group of its own), splits it on commas outside nested parentheses and
    returns the first token made only of Latin letters, digits and ``#%-``
    (with at least one letter, so footnote marks like ``(1)`` are not
    abbreviations). A token glued to other characters (``25-(OH)D``) is not
    an abbreviation. Cyrillic look-alikes are deliberately not folded here.

    Args:
        name: Analyte name, e.g. ``'Нейтрофіли (NEUT%)'``.

    Returns:
        The abbreviation, or ``None`` if there is none.
    """
    groups = _top_level_groups(name)
    if not groups:
        return None
    for token in (t.strip() for t in _split_top_level(groups[-1])):
        if _LATIN_TOKEN_RE.fullmatch(token) and re.search(r'[A-Za-z]', token):
            return token
    return None


def _top_level_groups(text: str) -> list[str]:
    """Contents of the closed top-level ``(…)`` groups, left to right."""
    groups: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char == '(':
            if depth == 0:
                start = index + 1
            depth += 1
        elif char == ')' and depth > 0:
            depth -= 1
            if depth == 0:
                groups.append(text[start:index])
    return groups


def _split_top_level(text: str) -> list[str]:
    """Split on commas that are not inside nested parentheses."""
    parts: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char == '(':
            depth += 1
        elif char == ')' and depth > 0:
            depth -= 1
        elif char == ',' and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def age_in_months(birth: date, on: date) -> int:
    """Count whole calendar months between a birth date and another date.

    Args:
        birth: Date of birth.
        on: Date the age is measured on.

    Returns:
        Whole months elapsed (floor semantics, so one day before ``birth`` is
        ``-1``); negative when ``on`` is before ``birth``.
    """
    months = (on.year - birth.year) * 12 + (on.month - birth.month)
    if on.day < birth.day:
        months -= 1
    return months
