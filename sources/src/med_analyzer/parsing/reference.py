"""Parsing of reference-range cells and selection of the group for a person.

A reference cell is either a single range (``130 - 160``, ``до 15``, ``≥1.2``)
or a Synevo block of lines, each line being ``<label>: <label>: <bounds>``.
Labels are a subject (``Діти``, ``Дорослі``, ``Новонароджені``, ``Чоловіки``,
``Жінки``), an age expression (``1-3 роки``, ``<1 року``, ``>50 років``) or a
subject with an age expression (``Чоловіки (17-50 років)``). A label-only line
(``Дорослі:``) scopes every line below it; the leading labels of a value line
(``Діти: <1 року: …``) and a ``Діти``/``Дорослі`` value line scope only the
bare age lines that follow. A label wrapped inside parentheses across lines
is joined back first.

Being wrong is worse than being silent here, so the parser is strict:

* a recognised subject with an unknown qualifier (``Чоловіки (курці)``), or a
  recognised label with an unparseable value or with an age band outside its
  scope, becomes a group with no bounds, so selecting it, or anything
  overlapping it, yields no range;
* a label that is neither a subject nor an age expression (``Дефіцит``,
  ``І триместр``) never becomes a group;
* after the first group, an unrecognised ``…:`` header, a ``*`` footnote or a
  ``При …`` line ends parsing for the rest of the block; an unrecognised
  header naming a population (``Вагітні жінки:``) ends it even before;
* a block with a ``<threshold> - <words>`` line (risk categories) has no
  groups at all.
"""

import math
import re
from dataclasses import dataclass
from decimal import Decimal

from med_analyzer.parsing.primitives import parse_decimal
from med_analyzer.parsing.types import ParsedReference, ReferenceGroup

ADULT_FROM_MONTHS = 216
_DAYS_PER_MONTH = 365.25 / 12

_NUM = r'[+-]?\d+(?:[.,]\d+)?'
_RANGE_RE = re.compile(rf'({_NUM})\s*-\s*({_NUM})')
_HIGH_RE = re.compile(rf'(?:до|<|≤)\s*({_NUM})', re.IGNORECASE)
_LOW_RE = re.compile(rf'(?:від|>|≥)\s*({_NUM})', re.IGNORECASE)
_THRESHOLD_RE = re.compile(r'[<≤>≥]\s*\d+(?:[.,]\d+)?\s*-\s*\D')

_UNIT = r'(років|роки|року|рік|міс\.?|днів|дн\.?)'
_AGE_RANGE_RE = re.compile(rf'(\d+)\s*{_UNIT}?\s*-\s*(\d+)\s*{_UNIT}', re.IGNORECASE)
_AGE_BETWEEN_RE = re.compile(
    rf'від\s*(\d+)\s*{_UNIT}\s*до\s*(\d+)\s*{_UNIT}', re.IGNORECASE
)
_AGE_BELOW_RE = re.compile(rf'(?:<|до)\s*(\d+)\s*{_UNIT}', re.IGNORECASE)
_AGE_FROM_RE = re.compile(rf'(?:>|≥|від)\s*(\d+)\s*{_UNIT}', re.IGNORECASE)

_SUBJECT_RE = re.compile(
    r'(діти|дорослі|новонароджені|чоловіки|жінки)(?!\w)\s*(.*)', re.IGNORECASE
)
# An unknown header that names a population (``Вагітні жінки:``) scopes the
# lines below it to people we cannot identify, unlike a neutral preface such
# as ``Нормативні значення (метод Вестергрена):``.
_POPULATION_RE = re.compile(r'жінк|чоловік|діт|дорос|вагітн|новонарод', re.IGNORECASE)


@dataclass(frozen=True)
class _Cond:
    """Sex and age-band condition of a label or of the current context.

    ``reset`` marks the top-level subjects ``Діти``/``Дорослі``: they are
    siblings of the current context, not nested in it.
    """

    sex: str | None = None
    age_from: int | None = None
    age_to: int | None = None
    reset: bool = False


# Lower-cased subject word -> the condition it stands for.
_SUBJECTS: dict[str, _Cond] = {
    'діти': _Cond(None, 0, ADULT_FROM_MONTHS, True),
    'дорослі': _Cond(None, ADULT_FROM_MONTHS, None, True),
    'новонароджені': _Cond(None, 0, 1),
    'чоловіки': _Cond('M'),
    'жінки': _Cond('F'),
}

_ROOT = _Cond()


def _normalise(line: str) -> str:
    for dash in ('–', '—', '−'):
        line = line.replace(dash, '-')
    return ' '.join(line.replace(' ', ' ').split())


def _join_wrapped(lines: list[str]) -> list[str]:
    """Join a line with an unclosed ``(`` to the lines that close it.

    A label wrapped inside its parentheses (``Чоловіки (18-50`` /
    ``років): …``) is otherwise lost. If the parenthesis never closes, the
    lines are left as they are rather than swallowing the rest of the block.
    """
    joined: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        j = i
        while line.count('(') > line.count(')') and j + 1 < len(lines):
            j += 1
            line = f'{line} {lines[j]}'
        if line.count('(') > line.count(')'):
            line, j = lines[i], i
        joined.append(line)
        i = j + 1
    return joined


def _split_labels(line: str) -> list[str]:
    """Split a line on ``:`` outside parentheses (labels may contain them)."""
    parts: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(line):
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth = max(depth - 1, 0)
        elif ch == ':' and depth == 0:
            parts.append(line[start:i].strip())
            start = i + 1
    parts.append(line[start:].strip())
    return parts


def _parse_bounds(text: str) -> tuple[Decimal | None, Decimal | None] | None:
    match = _RANGE_RE.fullmatch(text)
    if match:
        low, high = parse_decimal(match.group(1)), parse_decimal(match.group(2))
        return (low, high) if low <= high else None
    match = _HIGH_RE.fullmatch(text)
    if match:
        return None, parse_decimal(match.group(1))
    match = _LOW_RE.fullmatch(text)
    if match:
        return parse_decimal(match.group(1)), None
    return None


def _months(number: str, unit: str, upper: bool) -> int:
    n = int(number)
    unit = unit.lower()
    if unit.startswith('р'):
        return n * 12
    if unit.startswith('м'):
        return n
    # Days are rounded outward, so a band never covers less than printed;
    # an extra overlap only makes selection ambiguous, never wrong.
    days = n / _DAYS_PER_MONTH
    return math.ceil(days) if upper else math.floor(days)


def _intersect(
    a_from: int | None, a_to: int | None, b_from: int | None, b_to: int | None
) -> tuple[int | None, int | None] | None:
    froms = [x for x in (a_from, b_from) if x is not None]
    tos = [x for x in (a_to, b_to) if x is not None]
    age_from = max(froms) if froms else None
    age_to = min(tos) if tos else None
    if age_from is not None and age_to is not None and age_from >= age_to:
        return None
    return age_from, age_to


def _parse_age(text: str) -> tuple[int | None, int | None] | None:
    """Parse an age expression into a half-open month band.

    ``>N років`` is read as ``[N years, ∞)``: with ``17-50 років`` ending at
    50 years exclusive, the two bands then tile without a gap.
    """
    band: tuple[int | None, int | None]
    if match := _AGE_RANGE_RE.fullmatch(text):
        first, first_unit, second, second_unit = match.groups()
        band = (
            _months(first, first_unit or second_unit, upper=False),
            _months(second, second_unit, upper=True),
        )
    elif match := _AGE_BETWEEN_RE.fullmatch(text):
        band = (
            _months(match.group(1), match.group(2), upper=False),
            _months(match.group(3), match.group(4), upper=True),
        )
    elif match := _AGE_BELOW_RE.fullmatch(text):
        band = (0, _months(match.group(1), match.group(2), upper=True))
    elif match := _AGE_FROM_RE.fullmatch(text):
        band = (_months(match.group(1), match.group(2), upper=False), None)
    else:
        return None
    return _intersect(*band, None, None)


def _parse_label(text: str) -> tuple[_Cond, bool] | None:
    """Parse one label.

    Returns:
        ``(condition, resolved)``; ``resolved`` is false for a subject with
        a qualifier that is not understood. ``None`` for a category label.
    """
    match = _SUBJECT_RE.fullmatch(text)
    if not match:
        band = _parse_age(text)
        return None if band is None else (_Cond(None, *band), True)
    subject = _SUBJECTS[match.group(1).lower()]
    rest = match.group(2).strip()
    if rest.startswith('(') and rest.endswith(')'):
        rest = rest[1:-1].strip()
    if not rest:
        return subject, True
    band = _parse_age(rest)
    if band is not None:
        band = _intersect(subject.age_from, subject.age_to, *band)
    if band is None:
        return subject, False
    return _Cond(subject.sex, *band, subject.reset), True


def _combine(context: _Cond | None, cond: _Cond) -> _Cond | None:
    """Apply a label to the context; ``None`` if the age bands do not overlap."""
    if cond.reset:
        return cond
    if context is None:
        return None
    band = _intersect(context.age_from, context.age_to, cond.age_from, cond.age_to)
    if band is None:
        return None
    return _Cond(cond.sex or context.sex, *band)


def _ends_block(line: str, starred: bool) -> bool:
    return starred or line.lower().startswith('при ')


def parse_reference(text: str) -> ParsedReference:
    """Parse a reference cell into reference groups.

    Args:
        text: The reference cell, its lines joined with ``\\n``.

    Returns:
        The raw text and every group recognised in it. Never raises:
        unrecognised text simply yields no groups.
    """
    lines = _join_wrapped(
        [line for line in (_normalise(raw) for raw in text.split('\n')) if line]
    )
    if any(_THRESHOLD_RE.match(line.lstrip('*').strip()) for line in lines):
        return ParsedReference(raw=text, groups=[])

    groups: list[ReferenceGroup] = []
    # Two scopes: ``header`` comes from label-only lines (``Дорослі:``) and
    # applies to every line below; ``inline`` comes from a value line such as
    # ``Діти: <1 року: …`` or ``Дорослі: 0.27 - 4.2`` and applies only to the
    # bare age lines right after it (``1-6 років: …``), never to sex lines.
    header: tuple[_Cond | None, str] = (_ROOT, '')
    inline: tuple[_Cond | None, str] | None = None
    for index, line in enumerate(lines):
        starred = line.startswith('*')
        line = line.lstrip('*').strip()
        parts = _split_labels(line)

        if len(parts) == 1:
            # A bare range is a group only as the first line; further down it
            # is the wrapped value of the previous line.
            bounds = _parse_bounds(line) if index == 0 else None
            if bounds is not None:
                groups.append(ReferenceGroup(None, None, None, *bounds, line))
            elif groups and _ends_block(line, starred):
                break
            continue

        labels, value = parts[:-1], parts[-1]
        parsed = [_parse_label(label) for label in labels]
        if any(p is None for p in parsed):
            # Unrecognised header or category line. Before the first group a
            # population-free header is a neutral preface; any other one
            # opens a section (pregnancy, iron deficiency…) that is not ours.
            if not value and (groups or _POPULATION_RE.search(line)):
                break
            if groups and _ends_block(line, starred):
                break
            continue

        conds = [cond for cond, _ in parsed]
        oks = [ok for _, ok in parsed]
        if not value:
            # A header we only half understand would mis-scope every line
            # below it, so it ends the block.
            if not all(oks):
                break
            header = _fold(*header, conds, labels)
            inline = None
            continue

        subject_first = _SUBJECT_RE.fullmatch(labels[0]) is not None
        base = header if subject_first or inline is None else inline
        prefix = _fold(*base, conds[:-1], labels[:-1])
        group_cond, group_label = _fold(*prefix, conds[-1:], labels[-1:])
        resolved = all(oks)
        if len(labels) > 1:
            inline = prefix if all(oks[:-1]) else None
        elif subject_first:
            inline = (conds[0], labels[0]) if conds[0].reset and oks[0] else None
        if group_cond is None:
            # The label contradicts its scope; keep the printed line as a
            # group without bounds so it can only make selection ambiguous.
            group_cond = _combine(_ROOT, conds[-1])
            group_label = ': '.join(labels)
            resolved = False
        bounds = _parse_bounds(value) if resolved else None
        low, high = bounds if bounds is not None else (None, None)
        groups.append(
            ReferenceGroup(
                group_cond.sex,
                group_cond.age_from,
                group_cond.age_to,
                low,
                high,
                group_label,
            )
        )
    return ParsedReference(raw=text, groups=groups)


def _fold(
    context: _Cond | None, context_label: str, conds: list[_Cond], labels: list[str]
) -> tuple[_Cond | None, str]:
    """Apply labels left to right to a context and its printable label."""
    for cond, label in zip(conds, labels, strict=True):
        context = _combine(context, cond)
        if cond.reset or not context_label:
            context_label = label
        else:
            context_label = f'{context_label}: {label}'
    return context, context_label


def select_reference(
    ref: ParsedReference, sex: str | None, age_months: int | None
) -> tuple[Decimal | None, Decimal | None]:
    """Pick the reference range that applies to a person.

    A group matches when its sex is unset or equal to ``sex`` and the age
    falls into its half-open band (a missing side is open). If sex-specific
    and sex-neutral groups both match, the sex-specific ones count only when
    their age bands lie within every matching neutral band; otherwise the
    result is ambiguous. A group without an age band in a block that has
    age bands is treated as an adult line and is never selected for a child.

    Args:
        ref: The parsed reference.
        sex: ``'M'``, ``'F'`` or ``None`` when unknown.
        age_months: Age in whole months at collection, or ``None``.

    Returns:
        ``(low, high)`` of the single matching group, or ``(None, None)`` when
        the age is unknown or negative, or zero or several groups match.
    """
    if age_months is None or age_months < 0:
        return None, None
    matches = [
        group
        for group in ref.groups
        if (group.sex is None or (sex is not None and group.sex == sex))
        and (group.age_from_months is None or age_months >= group.age_from_months)
        and (group.age_to_months is None or age_months < group.age_to_months)
    ]
    specific = [group for group in matches if group.sex is not None]
    neutral = [group for group in matches if group.sex is None]
    if specific and neutral:
        # A sex line refines a neutral one only when it is nested in it
        # (``Дорослі:`` / ``Чоловіки:``); an unbounded ``Чоловіки:`` next to
        # ``Діти до 16 років:`` is a different population, not a refinement.
        if not all(_within(s, n) for s in specific for n in neutral):
            return None, None
        matches = specific
    if len(matches) != 1:
        return None, None
    match = matches[0]
    # A group without any age band in a block that does have age bands is,
    # in practice, the adult line (e.g. sex lines after child bands with no
    # ``Дорослі:`` header); its age range is not printed, so a child never
    # gets it.
    banded = any(_has_band(group) for group in ref.groups)
    if banded and not _has_band(match) and age_months < ADULT_FROM_MONTHS:
        return None, None
    return match.low, match.high


def _has_band(group: ReferenceGroup) -> bool:
    return group.age_from_months is not None or group.age_to_months is not None


def _within(inner: ReferenceGroup, outer: ReferenceGroup) -> bool:
    """Check that the age band of ``inner`` lies inside that of ``outer``."""
    inner_from = inner.age_from_months or 0
    outer_from = outer.age_from_months or 0
    if inner_from < outer_from:
        return False
    if outer.age_to_months is None:
        return True
    return (
        inner.age_to_months is not None and inner.age_to_months <= outer.age_to_months
    )
