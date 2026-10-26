"""Unit tests for reference range parsing and group selection.

The multi-line blocks are copied verbatim from the reference column of the
sample reports (no personal data). Ages are in months.
"""

import random
from decimal import Decimal

import pytest

from med_analyzer.parsing.reference import parse_reference, select_reference
from med_analyzer.parsing.types import ParsedReference

NONE = (None, None)

CREATININE = """Діти:
Новонароджені: 21.0 - 75.0
2-12 міс.: 15.0 - 37.0
1-3 роки: 21.0 - 36.0
3-5 років: 27.0 - 42.0
5-7 років: 28.0 - 52.0
7-9 років: 35.0 - 53.0
9-11 років: 34.0 - 65.0
11-13 років: 46.0 - 70.0
13-15 років: 50.0 - 77.0
Дорослі:
Жінки: 44.0 - 80.0
Чоловіки: 62.0 - 106.0"""

GLUCOSE = """Новонароджені діти (1 день життя): 2.22 - 3.33
Новонароджені діти (>1 дня життя): 2.78 - 4.44
Діти: 3.33 - 5.55
Дорослі:
18-60 років: 4.11 - 5.89
60-90 років: 4.56 - 6.38
>90 років: 4.16 - 6.72"""

FERRITIN = """Чоловіки (20-60 років): 30.0 - 400.0
Жінки (17-60 років): 13.0 - 150.0
При дефіциті заліза (залізодефіцитний стан)*:
Діти до 5 років: <12
Від 5 років до 20 років: <15
Вагітні жінки (для першого триместру
вагітності): <15
При надлишку заліза*:
Чоловіки (5–20 років): >200
Жінки (5–20 років): >150
* Враховуючи рекомендації Всесвітньої
організації охорони здоров’я ( WHO guideline: use
of ferratin concentrations to assess iron status in
individuals and populations, 2020)"""

ESR = """Нормативні значення (метод Вестергрена):
Діти <17 років: <10.0
Чоловіки (17-50 років): <15.0
Чоловіки >50 років: <20.0
Жінки (17 - 50 років): до 20.0
Жінки >50 років: до 30.0"""

TSH = """Діти:
0-12 міс.: 1.36 - 8.8
1-6 років: 0.85 - 6.5
7-12 років: 0.28 - 4.3
Дорослі: 0.27 - 4.2"""

HGB = """Діти: <1 року: 100.0 - 140.0
1-6 років: 110.0 - 145.0
6-16 років: 115.0 - 150.0
Дорослі:
Чоловіки: 130.0 - 160.0
Жінки: 120.0 - 140.0"""

NEUTROPHILS = """Дорослі:
Жінки: 1.56 – 6.13
Чоловіки: 1.78 – 5.38"""

AST = """Чоловіки: <40.0
Жінки: <32.0"""

IMMATURE_GRANULOCYTES = """*Діти: <2 років: ≤0.4
2-5 років: ≤0.3
5-10 років: ≤0.4
10-18 років: ≤1.1
Дорослі: 18-20 років: ≤1.1
20-60 років: ≤0.9
>60 років: ≤1.1
*Згідно наданих рекомендацій Roehrl et al.,
Reference Ranges for Immature
GranulocytesArch PatholLab Med—Vol 135, 2011
**Вагітні жінки:
І триместр: ≤0.92
ІІ триместр: ≤2.00
ІІІ триместр: ≤3.80
**Згідно наданих рекомендацій Ling-Ling Yuet
al., Changes and reference intervals of immature"""

HDL = """Жінки: ≥1.2
Чоловіки: ≥1.0
Цільові рівні згідно з рекомендаціями ESC/EAS
2016"""

CHOLESTEROL = """<5.0 - відсутність ризику
<4.5 - для людей з ішемічною хворобою серця,
цукровим діабетом
Цільові рівні згідно з рекомендаціями WHO
2012/ESC та EASD 2007"""

TRIGLYCERIDES = """<1.7 - низький рівень ризику
Цільові рівні згідно з рекомендаціями ESC/EAS
2019"""

LDL = """<3.0 - для груп низького ризику
<2.6 - для груп помірного ризику
<1.8 - для груп високого ризику
<1.4 - для груп дуже високого ризику
Цільові рівні згідно з рекомендаціями ESC/EAS
2019"""

VITAMIN_D = """Дорослі (від 18 років):
Дефіцит: <20.0
Недостатність: ≥20.0 - <30.0
Достатній рівень: ≥30.0 – 50.0*
Безпечний рівень, але не цільовий: ≥50.0 –
<60.0
Зона невизначеності з потенційними
перевагами
чи ризиками: ≥60.0 – <100.0
Надлишковий рівень/зона токсичності: ≥100.0
* Діагностика, профілактика та лікування
дефіциту вітаміну D у дорослих: Kонсенсус
українських експертів, 2023р
Діти (до 18 років):
Дефіцит: <20.0
Оптимальний рівень: ≥20.0**
**Враховуючи рекомендації Американської
Академії Педіатрії (American Academy of
Pediatrics, Wagner CL et al., Pediatrics 2008"""


def d(value: str | None) -> Decimal | None:
    """Build a Decimal, passing ``None`` through."""
    return None if value is None else Decimal(value)


def pick(text: str, sex: str | None, age_months: int | None) -> tuple:
    """Parse a reference text and select the range for a person."""
    return select_reference(parse_reference(text), sex, age_months)


@pytest.mark.parametrize(
    ('text', 'low', 'high'),
    [
        # Lab Service: single line, decimal comma.
        ('130 - 160', '130', '160'),
        ('до 15', None, '15'),
        ('4-9', '4', '9'),
        ('0,5 - 5', '0.5', '5'),
        ('0-1', '0', '1'),
        ('0,15 - 0,35', '0.15', '0.35'),
        ('34,5 - 49,5', '34.5', '49.5'),
        ('0 - 0,06', '0', '0.06'),
        # Synevo: single line, dot, en dash and operators.
        ('<20.0', None, '20.0'),
        ('<3.0', None, '3.0'),
        ('32.0 - 36.0', '32.0', '36.0'),
        ('0.01 – 0.08', '0.01', '0.08'),
        ('0.1 - 0.5', '0.1', '0.5'),
        ('35.0 - 46.0', '35.0', '46.0'),
        ('≤0.4', None, '0.4'),
        ('≥1.2', '1.2', None),
        ('>200', '200', None),
        ('від 5', '5', None),
    ],
)
def test_single_line(text: str, low: str | None, high: str | None) -> None:
    """Single-line references apply to anyone of any age and sex."""
    assert pick(text, 'M', 408) == (d(low), d(high))
    assert pick(text, None, 0) == (d(low), d(high))


def test_raw_is_kept() -> None:
    """The raw text is stored exactly as received."""
    assert parse_reference(ESR).raw == ESR


def test_nbsp_is_normalised() -> None:
    """NBSP inside a range does not break parsing."""
    assert pick('130 - 160', 'F', 408) == (d('130'), d('160'))


@pytest.mark.parametrize(
    ('sex', 'age', 'expected'),
    [
        ('M', 408, ('62.0', '106.0')),
        ('F', 408, ('44.0', '80.0')),
        ('M', 36, ('27.0', '42.0')),
        ('F', 35, ('21.0', '36.0')),
        (None, 12, ('21.0', '36.0')),
        ('M', 0, ('21.0', '75.0')),
        ('M', 2, ('15.0', '37.0')),
        ('M', 179, ('50.0', '77.0')),
    ],
)
def test_creatinine(sex: str | None, age: int, expected: tuple[str, str]) -> None:
    """Child age bands are half-open; adults are split by sex."""
    assert pick(CREATININE, sex, age) == (d(expected[0]), d(expected[1]))


@pytest.mark.parametrize('age', [1, 180, 204, 215])
def test_creatinine_gaps(age: int) -> None:
    """Ages no group covers (1 month, 15-18 years) give no range."""
    assert pick(CREATININE, 'M', age) == NONE


def test_creatinine_adult_without_sex() -> None:
    """A sex-split adult block needs the sex."""
    assert pick(CREATININE, None, 408) == NONE


@pytest.mark.parametrize(
    ('age', 'expected'),
    [
        (0, ('1.36', '8.8')),
        (11, ('1.36', '8.8')),
        (12, ('0.85', '6.5')),
        (216, ('0.27', '4.2')),
        (720, ('0.27', '4.2')),
    ],
)
def test_tsh(age: int, expected: tuple[str, str]) -> None:
    """The ``Дорослі:`` line after a ``Діти:`` block is a sibling group."""
    assert pick(TSH, 'F', age) == (d(expected[0]), d(expected[1]))


@pytest.mark.parametrize('age', [72, 83, 204])
def test_tsh_gaps(age: int) -> None:
    """6-7 years and 12-18 years are covered by no group."""
    assert pick(TSH, 'M', age) == NONE


@pytest.mark.parametrize(
    ('age', 'expected'),
    [
        (408, ('4.11', '5.89')),
        (216, ('4.11', '5.89')),
        (720, ('4.56', '6.38')),
        (1080, ('4.16', '6.72')),
        (132, ('3.33', '5.55')),
    ],
)
def test_glucose(age: int, expected: tuple[str, str]) -> None:
    """Glucose adult age bands nest under ``Дорослі:``."""
    assert pick(GLUCOSE, 'M', age) == (d(expected[0]), d(expected[1]))


def test_glucose_newborn_is_ambiguous() -> None:
    """At age 0 the newborn lines overlap ``Діти`` and no range is guessed."""
    assert pick(GLUCOSE, 'M', 0) == NONE


@pytest.mark.parametrize(
    ('sex', 'age', 'expected'),
    [
        ('M', 408, (None, '15.0')),
        ('M', 660, (None, '20.0')),
        ('M', 600, (None, '20.0')),
        ('M', 204, (None, '15.0')),
        ('F', 480, (None, '20.0')),
        ('F', 720, (None, '30.0')),
        ('F', 132, (None, '10.0')),
        (None, 132, (None, '10.0')),
    ],
)
def test_esr(
    sex: str | None, age: int, expected: tuple[str | None, str | None]
) -> None:
    """The ESR preface line is skipped; sex and age are combined."""
    assert pick(ESR, sex, age) == (d(expected[0]), d(expected[1]))


@pytest.mark.parametrize(
    ('sex', 'age', 'expected'),
    [
        ('M', 408, ('30.0', '400.0')),
        ('F', 300, ('13.0', '150.0')),
        ('F', 204, ('13.0', '150.0')),
        ('M', 132, (None, None)),
        ('F', 132, (None, None)),
        ('M', 228, (None, None)),
        ('M', 720, (None, None)),
    ],
)
def test_ferritin(sex: str, age: int, expected: tuple[str | None, str | None]) -> None:
    """Everything from ``При дефіциті заліза…:`` on is ignored."""
    assert pick(FERRITIN, sex, age) == (d(expected[0]), d(expected[1]))


def test_ferritin_ignored_section_makes_no_groups() -> None:
    """Only the two lines before the ignored section become groups."""
    assert len(parse_reference(FERRITIN).groups) == 2


@pytest.mark.parametrize(
    ('sex', 'age', 'expected'),
    [
        ('M', 0, ('100.0', '140.0')),
        ('F', 11, ('100.0', '140.0')),
        ('F', 12, ('110.0', '145.0')),
        ('M', 72, ('115.0', '150.0')),
        ('M', 408, ('130.0', '160.0')),
        ('F', 408, ('120.0', '140.0')),
        ('M', 192, (None, None)),
        (None, 408, (None, None)),
    ],
)
def test_hgb(
    sex: str | None, age: int, expected: tuple[str | None, str | None]
) -> None:
    """``Діти: <1 року:`` is an age, and it scopes the following lines."""
    assert pick(HGB, sex, age) == (d(expected[0]), d(expected[1]))


def test_neutrophils() -> None:
    """En dash ranges under ``Дорослі:`` with sex lines."""
    assert pick(NEUTROPHILS, 'F', 408) == (d('1.56'), d('6.13'))
    assert pick(NEUTROPHILS, 'M', 408) == (d('1.78'), d('5.38'))
    assert pick(NEUTROPHILS, 'M', 132) == NONE


def test_ast() -> None:
    """Sex-only groups without an age band."""
    assert pick(AST, 'M', 408) == (None, d('40.0'))
    assert pick(AST, 'F', 132) == (None, d('32.0'))
    assert pick(AST, None, 408) == NONE


def test_vitamin_b12_and_folate() -> None:
    """``Дорослі (20-78 років)`` narrows the adult band."""
    assert pick('Дорослі (20-78 років): 197.0 - 771.0', 'M', 408) == (
        d('197.0'),
        d('771.0'),
    )
    assert pick('Дорослі (20-78 років): 197.0 - 771.0', 'M', 216) == NONE
    assert pick('Дорослі (20-65 років): 3.89 - 26.8', 'F', 779) == (
        d('3.89'),
        d('26.8'),
    )
    assert pick('Дорослі (20-65 років): 3.89 - 26.8', 'F', 780) == NONE


@pytest.mark.parametrize(
    ('age', 'high'),
    [
        (0, '0.4'),
        (24, '0.3'),
        (60, '0.4'),
        (132, '1.1'),
        (216, '1.1'),
        (408, '0.9'),
        (720, '1.1'),
    ],
)
def test_immature_granulocytes(age: int, high: str) -> None:
    """Leading ``*`` is stripped; the pregnancy section is ignored."""
    assert pick(IMMATURE_GRANULOCYTES, 'F', age) == (None, d(high))


def test_immature_granulocytes_groups() -> None:
    """Trimester lines never become groups."""
    groups = parse_reference(IMMATURE_GRANULOCYTES).groups
    assert len(groups) == 7
    assert all('триместр' not in g.label for g in groups)


def test_hdl() -> None:
    """Lower-bound-only, sex-specific groups; trailing text is skipped."""
    assert pick(HDL, 'M', 408) == (d('1.0'), None)
    assert pick(HDL, 'F', 408) == (d('1.2'), None)
    assert pick(HDL, None, 408) == NONE


@pytest.mark.parametrize('text', [CHOLESTEROL, TRIGLYCERIDES, LDL, VITAMIN_D])
def test_threshold_style_has_no_groups(text: str) -> None:
    """Risk-category references carry no numeric range."""
    ref = parse_reference(text)
    assert ref.groups == []
    assert ref.raw == text
    assert select_reference(ref, 'M', 408) == NONE
    assert select_reference(ref, 'F', 132) == NONE


@pytest.mark.parametrize('age', [None, -1, -12])
def test_unknown_or_negative_age(age: int | None) -> None:
    """Without a usable age no range is selected, even a flat one."""
    assert pick('130 - 160', 'M', age) == NONE


def test_days_age_band() -> None:
    """Days are rounded outward to whole months."""
    group = parse_reference('Діти:\n0-6 днів: 1.0 - 2.0').groups[0]
    assert (group.age_from_months, group.age_to_months) == (0, 1)


def test_age_bands_in_months() -> None:
    """Spot-check the month conversion of the age expressions."""
    bands = [
        (g.sex, g.age_from_months, g.age_to_months) for g in parse_reference(ESR).groups
    ]
    assert bands == [
        (None, 0, 204),
        ('M', 204, 600),
        ('M', 600, None),
        ('F', 204, 600),
        ('F', 600, None),
    ]


def test_unknown_qualifier_is_never_guessed() -> None:
    """A recognised label with an unknown qualifier yields no range."""
    assert pick('Чоловіки (курці): 1.0 - 5.0', 'M', 408) == NONE
    assert pick('Дорослі: 1.0 - 5.0\nЖінки (вагітні): 2.0 - 6.0', 'F', 408) == NONE


def test_overlapping_groups_are_ambiguous() -> None:
    """Two sex-neutral groups covering the same age give no range."""
    assert pick('Дорослі: 1.0 - 5.0\n18-60 років: 2.0 - 6.0', 'M', 408) == NONE


@pytest.mark.parametrize(
    'text',
    [
        '',
        '   ',
        '\n\n',
        'Аналіз виконано на сучасному обладнанні, результат готовий.',
        ':::',
        '<1:40',
        'Діти:',
        '- - -',
        '((((:',
        'Нормально',
    ],
)
def test_junk_never_raises(text: str) -> None:
    """Arbitrary text parses to no groups instead of raising."""
    ref = parse_reference(text)
    assert isinstance(ref, ParsedReference)
    assert ref.raw == text
    assert ref.groups == []


# Regression probes from review round 1: realistic variants of the samples.

HGB_NO_ADULT_HEADER = """Діти: <1 року: 100.0 - 140.0
1-6 років: 110.0 - 145.0
6-16 років: 115.0 - 150.0
Чоловіки: 130.0 - 160.0
Жінки: 120.0 - 140.0"""

TSH_WITH_ELDERLY = """Діти:
0-12 міс.: 1.36 - 8.8
1-6 років: 0.85 - 6.5
Дорослі: 0.27 - 4.2
>60 років: 0.5 - 8.9"""

CHILD_BAND_AND_SEX_LINES = """Діти до 16 років: 115.0 - 150.0
Чоловіки: 130.0 - 160.0
Жінки: 120.0 - 140.0"""


@pytest.mark.parametrize(
    ('sex', 'age', 'expected'),
    [
        ('M', 132, (None, None)),
        ('F', 132, (None, None)),
        ('M', 204, (None, None)),
        ('F', 204, (None, None)),
        ('M', 408, ('130.0', '160.0')),
        ('F', 408, ('120.0', '140.0')),
        # The unbounded sex line also covers infants, so even the child band
        # is ambiguous for a known sex (strict nesting rule).
        ('M', 0, (None, None)),
        (None, 0, ('100.0', '140.0')),
    ],
)
def test_inline_children_scope_does_not_leak_into_sex_lines(
    sex: str | None, age: int, expected: tuple[str | None, str | None]
) -> None:
    """``Діти: <1 року:`` scopes only the bare age lines after it."""
    assert pick(HGB_NO_ADULT_HEADER, sex, age) == (d(expected[0]), d(expected[1]))


def test_sex_lines_after_children_are_not_scoped_to_children() -> None:
    """The sex lines keep no child band from the inline ``Діти`` label."""
    groups = parse_reference(HGB_NO_ADULT_HEADER).groups
    assert [(g.sex, g.age_from_months, g.age_to_months) for g in groups[3:]] == [
        ('M', None, None),
        ('F', None, None),
    ]


@pytest.mark.parametrize('age', [720, 732, 900])
def test_age_line_after_adult_value_line_is_not_dropped(age: int) -> None:
    """``>60 років`` after ``Дорослі: …`` overlaps it, so no range is picked."""
    assert pick(TSH_WITH_ELDERLY, 'M', age) == NONE


def test_adult_value_line_still_selected_below_the_overlap() -> None:
    """Between 18 and 60 only ``Дорослі`` covers the age."""
    assert pick(TSH_WITH_ELDERLY, 'M', 408) == (d('0.27'), d('4.2'))


def test_label_contradicting_scope_keeps_a_boundless_group() -> None:
    """A printed line whose band contradicts its scope is never dropped."""
    assert pick('Діти:\n0-12 міс.: 1.0 - 2.0\nДорослі: 3.0 - 4.0', 'M', 408) == (
        d('3.0'),
        d('4.0'),
    )
    contradicting = parse_reference('Дорослі:\nНовонароджені: 1.0 - 2.0').groups
    assert [
        (g.age_from_months, g.age_to_months, g.low, g.high) for g in contradicting
    ] == [(0, 1, None, None)]


@pytest.mark.parametrize(
    ('sex', 'age', 'expected'),
    [
        ('M', 132, (None, None)),
        ('F', 132, (None, None)),
        ('M', 408, ('130.0', '160.0')),
        ('F', 408, ('120.0', '140.0')),
    ],
)
def test_unbounded_sex_line_does_not_displace_child_band(
    sex: str, age: int, expected: tuple[str | None, str | None]
) -> None:
    """A sex line wins over a neutral one only when nested in its age band."""
    assert pick(CHILD_BAND_AND_SEX_LINES, sex, age) == (
        d(expected[0]),
        d(expected[1]),
    )


def test_wrapped_label_is_joined() -> None:
    """A label wrapped inside its parentheses keeps its line."""
    text = 'Дорослі: 1.0 - 5.0\nЧоловіки (18-50\nроків): 2.0 - 6.0'
    assert pick(text, 'M', 408) == (d('2.0'), d('6.0'))
    assert pick(text, 'F', 408) == (d('1.0'), d('5.0'))
    assert pick(text, 'M', 600) == (d('1.0'), d('5.0'))


def test_unclosed_parenthesis_does_not_swallow_the_block() -> None:
    """A parenthesis that never closes leaves the lines as they are."""
    text = 'Жінки: 1.0 - 2.0\nЧоловіки (примітка: 3.0 - 4.0'
    assert pick(text, 'F', 408) == (d('1.0'), d('2.0'))


def test_population_header_first_ends_the_block() -> None:
    """``Вагітні жінки:`` before any group is not a neutral preface."""
    ref = parse_reference('Вагітні жінки:\nЖінки: 1.0 - 2.0')
    assert ref.groups == []
    assert select_reference(ref, 'F', 408) == NONE


def test_neutral_preface_is_still_skipped() -> None:
    """A preface naming no population is skipped (the ESR method line)."""
    assert pick(ESR, 'M', 408) == (None, d('15.0'))


def test_plus_sign_is_accepted() -> None:
    """An explicit ``+`` sign on a bound parses."""
    assert pick('-2.0 - +2.0', 'M', 408) == (d('-2.0'), d('2.0'))


def test_seeded_fuzz_never_raises() -> None:
    """Random keyword soup never raises, in parsing or in selection."""
    rng = random.Random(20261022)
    tokens = [
        'Діти',
        'Дорослі',
        'Чоловіки',
        'Жінки',
        'Новонароджені',
        'Вагітні',
        'При',
        'років',
        'роки',
        'року',
        'міс.',
        'днів',
        'дн.',
        'до',
        'від',
        '<',
        '>',
        '≤',
        '≥',
        '-',
        '–',
        '—',
        ':',
        '(',
        ')',
        '*',
        '**',
        '\n',
        ' ',
        ' ',
        '0',
        '1',
        '12',
        '18',
        '60',
        '5,5',
        '0.27',
        '+',
        ',',
    ]
    for _ in range(2000):
        text = ''.join(rng.choice(tokens) for _ in range(rng.randint(0, 30)))
        ref = parse_reference(text)
        assert ref.raw == text
        sex = rng.choice(['M', 'F', None])
        age = rng.choice([None, -1, 0, 1, 12, 36, 204, 216, 408, 720, 1200])
        low, high = select_reference(ref, sex, age)
        assert low is None or high is None or low <= high
