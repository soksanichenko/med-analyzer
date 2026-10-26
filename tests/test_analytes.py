"""Tests for analyte normalisation, the seed dictionary and matching."""

from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from med_analyzer import analytes
from med_analyzer.analyte_seed import SEED
from med_analyzer.analytes import (
    match_analyte,
    normalize_abbreviation,
    normalize_name,
    seed_analytes,
)
from med_analyzer.models import Analyte, AnalyteAlias
from med_analyzer.parsing.lab_service import parse_lab_service
from med_analyzer.parsing.synevo import parse_synevo
from med_analyzer.parsing.types import ParsedResult
from med_analyzer.parsing.words import pages_from_json

FIXTURES = Path(__file__).parent / 'fixtures'

# Look-alike letters are written as escapes so the file stays unambiguous.
CYR_HCT = '\u041d\u0421\u0422'  # Cyrillic En, Es, Te: looks like HCT
CYR_MCH = '\u041c\u0421\u041d'  # Cyrillic Em, Es, En: looks like MCH
CYR_MCHC = '\u041c\u0421\u041d\u0421'
CYR_PCT = '\u0420\u0421\u0422'  # Cyrillic Er, Es, Te: looks like PCT
APOS = '\u2019'  # typographic apostrophe, as printed by Lab Service

# Expected analyte code for every row of the three fixtures, written by hand
# and independent of the matcher. ``None`` marks a row that is not an analyte.
LAB_SERVICE_CODES = {
    'Швидкість осідання еритроцитів за Вестергреном': 'ESR',
    'Кількість лейкоцитів (WBC)': 'WBC',
    'Кількість еритроцитів (RBC)': 'RBC',
    'Концентрація гемоглобіну (HGB)': 'HGB',
    'Кількість тромбоцитів (PLT)': 'PLT',
    'Нейтрофіли (NEUT%)': 'NEUT%',
    'Лімфоцити (LYM%)': 'LYM%',
    'Моноцити (MONO%)': 'MONO%',
    'Еозинофіли (EO%)': 'EO%',
    'Базофіли (BASO%)': 'BASO%',
    'Незрілі гранулоцити (IMG%)': 'IMG%',
    'Абсолютна кількість нейтрофілів (NEUT#)': 'NEUT#',
    'Абсолютна кількість лімфоцитів (LYM#)': 'LYM#',
    'Абсолютна кількість моноцитів (MONO#)': 'MONO#',
    'Абсолютна кількість еозинофілів (EO#)': 'EO#',
    'Абсолютна кількість базофілів (BASO#)': 'BASO#',
    'Абсолютна кількість незрілих гранулоцитів (IMG#)': 'IMG#',
    'Гематокрит (HCT)': 'HCT',
    f'Середній об{APOS}єм еритроцитів (MCV)': 'MCV',
    'Середній вміст гемоглобіну в еритроциті (MCH)': 'MCH',
    'Середня концентрація гемоглобіну в еритроциті (MCHC)': 'MCHC',
    'Ширина розподілу еритроцитів, стандартне відхилення (RDW-SD)': 'RDW-SD',
    'Ширина розподілу еритроцитів, коефіцієнт варіації (RDW-CV)': 'RDW-CV',
    'Тромбокрит (PCT)': 'PCT',
    f'Середній об{APOS}єм тромбоцитів (MPV)': 'MPV',
    'Відношення кількості великих тромбоцитів до загальної кількості '
    'тромбоцитів (P-LCR)': 'P-LCR',
    'Ширина розподілу тромбоцитів (PDW)': 'PDW',
}
SYNEVO_CODES = {
    'Креатинін': 'CREA',
    'Пероксидаза щитоподібної залози, антитіла (АТПО)': 'TPO-AB',
    'Глюкоза (сироватка)': 'GLU',
    'Ціанокобаламін (вітамін B12)': 'B12',
    'Феритин': 'FERR',
    'Фолієва кислота (сироватка)': 'FOLATE',
    'Вітамін Д (25-гідроксивітамін D, 25- (OH)D)': 'VITD',
    'Холестерин': 'CHOL',
    'Тригліцериди': 'TRIG',
    'Ліпопротеїди високої щільності (ЛПВЩ, HDL)': 'HDL',
    'Ліпопротеїди низької щільності (ЛПНЩ, LDL)': 'LDL',
    'Ліпопротеїди дуже низької щільності (VLDL)': 'VLDL',
    'Холестерин не-Ліпопротеїнів високої щільності (ХС не-ЛПВЩ)': 'NONHDL',
    'Коефіцієнт атерогенності (КА)': 'AI',
    'Звіт з описом лабораторних показників': None,
    'Тиреотропний гормон. ВИСОКОЧУТЛИВИЙ, БІОТИН-НЕЗАЛЕЖНИЙ': 'TSH',
    'Тироксин вільний (T4 вільний)': 'FT4',
    'Трийодтиронін вільний (T3 вільний)': 'FT3',
    'Аспартатамінотрансфераза (АСТ, AST)': 'AST',
    'Аланінамінотрансфераза (АЛТ, ALT)': 'ALT',
    'Аналіз крові: Швидкість осідання еритроцитів (ШОЕ, ESR)': 'ESR',
    'Лейкоцити (WBC)': 'WBC',
    'Еритроцити (RBC)': 'RBC',
    'Гемоглобін (HGB)': 'HGB',
    'Гематокрит (НСТ)': 'HCT',
    "Середній об'єм еритроцита (MCV)": 'MCV',
    'Середній вміст гемоглобіну в еритроциті (МСН)': 'MCH',
    'Середня концентрація гемоглобіну в еритроциті (МСНС)': 'MCHC',
    'Тромбоцити (PLT)': 'PLT',
    'Ширина розподілення еритроцитів (RDW-SD)': 'RDW-SD',
    'Ширина розподілення еритроцитів (RDW-CV)': 'RDW-CV',
    'Ширина розподілення тромбоцитів за обсягами (PDW)': 'PDW',
    "Середній об'єм тромбоцитів (MPV)": 'MPV',
    'Тромбокрит (РСТ)': 'PCT',
    'Нейтрофіли (на 100 лейкоцитів)': 'NEUT%',
    'Нейтрофіли (абс.)': 'NEUT#',
    'Лімфоцити (на 100 лейкоцитів)': 'LYM%',
    'Лімфоцити (абс.)': 'LYM#',
    'Моноцити (на 100 лейкоцитів)': 'MONO%',
    'Моноцити (абс.)': 'MONO#',
    'Еозинофіли (на 100 лейкоцитів)': 'EO%',
    'Еозинофіли (абс.)': 'EO#',
    'Базофіли (на 100 лейкоцитів)': 'BASO%',
    'Базофіли (абс.)': 'BASO#',
    'Незрілі гранулоцити (на 100 лейкоцитів)': 'IMG%',
    'Незрілі гранулоцити (абс.)': 'IMG#',
}
# A pointer to a separate PDF, not an analyte: it is the one row that falls
# through to auto-creation.
NOT_AN_ANALYTE = 'Звіт з описом лабораторних показників'

HEMATOLOGY_CODES = [
    'WBC',
    'RBC',
    'HGB',
    'HCT',
    'MCV',
    'MCH',
    'MCHC',
    'PLT',
    'RDW-SD',
    'RDW-CV',
    'PDW',
    'MPV',
    'PCT',
    'NEUT%',
    'NEUT#',
    'LYM%',
    'LYM#',
    'MONO%',
    'MONO#',
    'EO%',
    'EO#',
    'BASO%',
    'BASO#',
    'IMG%',
    'IMG#',
    'ESR',
]


def _parse(parser: Callable, name: str) -> list[ParsedResult]:
    pages = pages_from_json((FIXTURES / name).read_text(encoding='utf-8'))
    return parser(pages).results


@pytest.fixture(scope='module')
def lab_service_results() -> list[ParsedResult]:
    return _parse(parse_lab_service, 'lab_service.json')


@pytest.fixture(scope='module')
def synevo_results() -> list[ParsedResult]:
    return _parse(parse_synevo, 'synevo.json') + _parse(parse_synevo, 'synevo_2.json')


def _count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model))


# --- normalisation ---------------------------------------------------------


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('Гемоглобін (HGB)', 'гемоглобін (hgb)'),
        (f'Середній об{APOS}єм', "середній об'єм"),
        ('Середній обʼєм', "середній об'єм"),
        ("Середній об'єм", "середній об'єм"),
        ('Середній об\u2018єм', "середній об'єм"),
        ('Середній об\u00b4єм', "середній об'єм"),
        ('Середній об`єм', "середній об'єм"),
        ('  Лейкоцити   (WBC)\n', 'лейкоцити (wbc)'),
        ('Аналіз крові: Лейкоцити (WBC)', 'лейкоцити (wbc)'),
        ('Аналіз крові:  Лейкоцити', 'лейкоцити'),
        ('Холестерин.', 'холестерин'),
        ('- Креатинін, ', 'креатинін'),
        ('Нейтрофіли (абс.)', 'нейтрофіли (абс.)'),
        ('Нейтрофіли (NEUT%)', 'нейтрофіли (neut%)'),
    ],
)
def test_normalize_name(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


def test_normalize_name_is_idempotent() -> None:
    once = normalize_name(f'Аналіз крові: Об{APOS}єм  (MCV).')
    assert normalize_name(once) == once


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('HGB', 'HGB'),
        (' hgb ', 'HGB'),
        ('neut%', 'NEUT%'),
        ('RDW-SD', 'RDW-SD'),
        (CYR_HCT, 'HCT'),
        (CYR_MCH, 'MCH'),
        (CYR_MCHC, 'MCHC'),
        (CYR_PCT, 'PCT'),
        (CYR_HCT.lower(), 'HCT'),
        # Letters without a Latin twin stay as they are (upper-cased).
        ('\u043b\u043f', '\u041b\u041f'),
    ],
)
def test_normalize_abbreviation(raw: str, expected: str) -> None:
    assert normalize_abbreviation(raw) == expected


def test_normalize_abbreviation_folds_all_eleven_lookalikes() -> None:
    cyrillic = '\u0410\u0412\u0415\u041a\u041c\u041d\u041e\u0420\u0421\u0422\u0425'
    assert normalize_abbreviation(cyrillic) == 'ABEKMHOPCTX'


# --- seed ------------------------------------------------------------------


def test_seed_codes_are_unique_and_canonical() -> None:
    codes = [item.code for item in SEED]
    assert len(codes) == len(set(codes))
    for code in codes:
        assert normalize_abbreviation(code) == code
    assert set(HEMATOLOGY_CODES) <= set(codes)


def test_seed_aliases_do_not_collide() -> None:
    seen: dict[str, str] = {}
    for item in SEED:
        for alias in item.aliases:
            key = normalize_name(alias)
            assert seen.setdefault(key, item.code) == item.code, key


def test_seed_analytes_is_idempotent(db: Session) -> None:
    seed_analytes(db)
    db.commit()
    analytes_before = _count(db, Analyte)
    aliases_before = _count(db, AnalyteAlias)
    assert analytes_before == len(SEED)

    seed_analytes(db)
    db.commit()
    assert _count(db, Analyte) == analytes_before
    assert _count(db, AnalyteAlias) == aliases_before


def test_seed_restores_missing_alias_and_keeps_other_analytes(db: Session) -> None:
    seed_analytes(db)
    esr = db.scalar(select(Analyte).where(Analyte.code == 'ESR'))
    aliases = select(AnalyteAlias).where(AnalyteAlias.analyte_id == esr.id)
    expected = len(db.scalars(aliases).all())
    assert expected > 0
    for alias in db.scalars(aliases):
        db.delete(alias)
    db.add(Analyte(code='custom', name='Custom'))
    db.flush()

    seed_analytes(db)

    assert db.scalar(select(Analyte).where(Analyte.code == 'custom')) is not None
    assert len(db.scalars(aliases).all()) == expected


# --- matching: fixtures ----------------------------------------------------


def test_expected_tables_cover_every_fixture_row(
    lab_service_results: list[ParsedResult],
    synevo_results: list[ParsedResult],
) -> None:
    assert {r.raw_name for r in lab_service_results} == set(LAB_SERVICE_CODES)
    assert {r.raw_name for r in synevo_results} == set(SYNEVO_CODES)


@pytest.mark.parametrize('code', HEMATOLOGY_CODES)
def test_both_labs_resolve_to_one_analyte(
    db: Session,
    code: str,
    lab_service_results: list[ParsedResult],
    synevo_results: list[ParsedResult],
) -> None:
    seed_analytes(db)
    ls_rows = [r for r in lab_service_results if LAB_SERVICE_CODES[r.raw_name] == code]
    sy_rows = [r for r in synevo_results if SYNEVO_CODES[r.raw_name] == code]
    assert len(ls_rows) == 1, 'Lab Service must have the analyte'
    assert len(sy_rows) == 1, 'Synevo must have the analyte'

    ls_analyte = match_analyte(db, ls_rows[0].raw_name, ls_rows[0].abbreviation)
    sy_analyte = match_analyte(db, sy_rows[0].raw_name, sy_rows[0].abbreviation)

    assert ls_analyte.id == sy_analyte.id
    assert ls_analyte.code == code


def test_all_fixture_rows_match_the_seed_without_creating_analytes(
    db: Session,
    lab_service_results: list[ParsedResult],
    synevo_results: list[ParsedResult],
) -> None:
    seed_analytes(db)
    db.commit()
    seeded = _count(db, Analyte)

    for result in [*lab_service_results, *synevo_results]:
        if result.raw_name == NOT_AN_ANALYTE:
            continue
        analyte = match_analyte(db, result.raw_name, result.abbreviation)
        expected = (
            LAB_SERVICE_CODES.get(result.raw_name) or SYNEVO_CODES[result.raw_name]
        )
        assert analyte.code == expected, result.raw_name

    assert _count(db, Analyte) == seeded


def test_report_pointer_row_is_the_only_auto_created_analyte(
    db: Session, synevo_results: list[ParsedResult]
) -> None:
    seed_analytes(db)
    db.commit()
    seeded = _count(db, Analyte)
    pointer = next(r for r in synevo_results if r.raw_name == NOT_AN_ANALYTE)

    match_analyte(db, pointer.raw_name, pointer.abbreviation)

    assert _count(db, Analyte) == seeded + 1


# --- matching: rules -------------------------------------------------------


def test_cyrillic_and_latin_hct_resolve_to_the_same_analyte(db: Session) -> None:
    seed_analytes(db)
    cyrillic = match_analyte(db, f'Гематокрит ({CYR_HCT})', CYR_HCT)
    latin = match_analyte(db, 'Гематокрит (HCT)', 'HCT')
    assert cyrillic.id == latin.id
    assert cyrillic.code == 'HCT'


@pytest.mark.parametrize(
    ('cyrillic', 'code'),
    [(CYR_HCT, 'HCT'), (CYR_MCH, 'MCH'), (CYR_MCHC, 'MCHC')],
)
def test_abbreviation_is_derived_from_name_when_parser_gave_none(
    db: Session, cyrillic: str, code: str
) -> None:
    seed_analytes(db)
    # A name that is in no alias list, so only the parenthesised group helps.
    analyte = match_analyte(db, f'Невідомий показник ({cyrillic})', None)
    assert analyte.code == code


def test_derived_abbreviation_is_used_when_given_one_is_unknown(db: Session) -> None:
    seed_analytes(db)
    analyte = match_analyte(db, f'Невідомий показник ({CYR_HCT})', 'XYZ')
    assert analyte.code == 'HCT'


def test_derived_abbreviation_uses_last_top_level_group(db: Session) -> None:
    seed_analytes(db)
    analyte = match_analyte(db, 'Показник (WBC) (ALT, інше)', None)
    assert analyte.code == 'ALT'


def test_bare_short_token_is_not_trusted(db: Session) -> None:
    seed_analytes(db)
    before = _count(db, Analyte)
    analyte = match_analyte(db, 'Невідомий показник (QZX)', 'QZX')
    assert analyte.code not in {item.code for item in SEED}
    assert _count(db, Analyte) == before + 1


@pytest.mark.parametrize('bogus', ['D', 'OH', '25', None])
def test_vitamin_d_resolves_by_name_alias_not_by_abbreviation(
    db: Session, bogus: str | None
) -> None:
    seed_analytes(db)
    before = _count(db, Analyte)
    analyte = match_analyte(db, 'Вітамін Д (25-гідроксивітамін D, 25- (OH)D)', bogus)
    assert analyte.code == 'VITD'
    assert _count(db, Analyte) == before


def test_unknown_name_creates_one_analyte_and_alias_once(db: Session) -> None:
    seed_analytes(db)
    analytes_before = _count(db, Analyte)
    aliases_before = _count(db, AnalyteAlias)

    first = match_analyte(db, 'Новий  показник (Тест)', None)
    second = match_analyte(db, 'новий показник (тест)', None)

    assert first.id == second.id
    assert first.name == 'Новий  показник (Тест)'
    assert first.code == 'новий показник (тест)'
    assert _count(db, Analyte) == analytes_before + 1
    assert _count(db, AnalyteAlias) == aliases_before + 1


def test_long_unknown_names_get_distinct_stable_codes(db: Session) -> None:
    prefix = 'дуже довга назва показника ' * 4
    one = match_analyte(db, prefix + 'перший', None)
    two = match_analyte(db, prefix + 'другий', None)
    again = match_analyte(db, prefix + 'перший', None)

    assert one.id != two.id
    assert one.id == again.id
    assert len(one.code) <= 64
    assert len(two.code) <= 64
    assert one.code != two.code


def test_match_flushes_but_does_not_commit(db: Session) -> None:
    analyte = match_analyte(db, 'Невідомий показник', None)
    assert analyte.id is not None
    db.rollback()
    assert _count(db, Analyte) == 0


def test_creating_an_already_existing_name_does_not_raise(db: Session) -> None:
    # The row appears between the lookup and the insert: simulate by calling
    # the creation step directly for a name that was inserted meanwhile.
    existing = match_analyte(db, 'Гонка за назвою', None)
    key = normalize_name('Гонка за назвою')

    again = analytes._create_analyte(db, 'Гонка за назвою', key)

    assert again.id == existing.id
    assert _count(db, Analyte) == 1


def test_stale_lookup_falls_back_to_reselect(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = match_analyte(db, 'Гонка за назвою', None)
    real = analytes._find_by_alias
    calls: list[str] = []

    def stale_once(session: Session, key: str) -> Analyte | None:
        calls.append(key)
        return None if len(calls) == 1 else real(session, key)

    monkeypatch.setattr(analytes, '_find_by_alias', stale_once)

    again = match_analyte(db, 'Гонка за назвою', None)

    assert again.id == existing.id
    assert _count(db, Analyte) == 1
    # The outer transaction is still usable after the savepoint rolled back.
    assert db.scalar(select(func.count()).select_from(AnalyteAlias)) == 1


def test_code_collision_gets_a_suffix(db: Session) -> None:
    taken = Analyte(code='колізія', name='Other')
    db.add(taken)
    db.flush()

    created = match_analyte(db, 'Колізія', None)

    assert created.id != taken.id
    assert created.code != 'колізія'
    assert created.code.startswith('колізія')


# --- review round 1 ----------------------------------------------------------


def test_lookalike_table_is_exactly_the_eleven_pairs() -> None:
    pairs = zip(
        (0x410, 0x412, 0x415, 0x41A, 0x41C, 0x41D, 0x41E, 0x420, 0x421, 0x422, 0x425),
        'ABEKMHOPCTX',
        strict=True,
    )
    for cyrillic, latin in pairs:
        assert normalize_abbreviation(chr(cyrillic)) == latin
        assert normalize_abbreviation(chr(cyrillic).lower()) == latin


def test_seed_codes_flagged_as_needing_a_name() -> None:
    assert {i.code for i in SEED if i.abbreviation_needs_name} == {'PCT', 'AI'}


def test_thyroglobulin_is_not_triglycerides(db: Session) -> None:
    seed_analytes(db)
    assert db.scalar(select(Analyte).where(Analyte.code == 'TG')) is None
    before = _count(db, Analyte)

    analyte = match_analyte(db, 'Тиреоглобулін (Tg)', 'Tg')

    assert analyte.code != 'TRIG'
    assert _count(db, Analyte) == before + 1
    assert match_analyte(db, 'Тригліцериди', None).code == 'TRIG'


@pytest.mark.parametrize('abbreviation', ['PCT', None])
def test_procalcitonin_is_not_plateletcrit(
    db: Session, abbreviation: str | None
) -> None:
    seed_analytes(db)
    before = _count(db, Analyte)

    analyte = match_analyte(db, 'Прокальцитонін (PCT)', abbreviation)

    assert analyte.code != 'PCT'
    assert _count(db, Analyte) == before + 1
    assert match_analyte(db, 'Прокальцитонін (PCT)', 'PCT').id == analyte.id


def test_procalcitonin_with_cyrillic_lookalikes_is_not_plateletcrit(
    db: Session,
) -> None:
    seed_analytes(db)
    analyte = match_analyte(db, f'Прокальцитонін ({CYR_PCT})', None)
    assert analyte.code != 'PCT'


@pytest.mark.parametrize(
    ('name', 'abbreviation'),
    [
        ('Тромбокрит (PCT)', 'PCT'),
        ('Тромбокрит (PCT)', None),
        (f'Тромбокрит ({CYR_PCT})', None),
        (f'Тромбокрит ({CYR_PCT})', CYR_PCT),
        ('Аналіз крові: Тромбокрит (PCT)', 'PCT'),
    ],
)
def test_plateletcrit_still_resolves_with_its_stem(
    db: Session, name: str, abbreviation: str | None
) -> None:
    seed_analytes(db)
    assert match_analyte(db, name, abbreviation).code == 'PCT'


def test_ai_needs_its_stem(db: Session) -> None:
    seed_analytes(db)
    assert match_analyte(db, 'Коефіцієнт атерогенності (КА)', None).code == 'AI'
    assert match_analyte(db, 'Коефіцієнт атерогенності (AI)', 'AI').code == 'AI'
    other = match_analyte(db, 'Штучний інтелект (AI)', 'AI')
    assert other.code != 'AI'


def test_display_names_are_not_aliases(db: Session) -> None:
    seed_analytes(db)
    registered = set(db.scalars(select(AnalyteAlias.alias)))
    for item in SEED:
        if normalize_name(item.name) not in {normalize_name(a) for a in item.aliases}:
            assert normalize_name(item.name) not in registered, item.code


def test_bare_leukocytes_is_not_wbc(db: Session) -> None:
    seed_analytes(db)
    before = _count(db, Analyte)

    bare = match_analyte(db, 'Лейкоцити', None)
    blood = match_analyte(db, 'Лейкоцити (WBC)', 'WBC')

    assert blood.code == 'WBC'
    assert bare.id != blood.id
    assert bare.code != 'WBC'
    assert _count(db, Analyte) == before + 1


@pytest.mark.parametrize('name', ['', '   ', '\u00a0\t'])
def test_empty_name_is_rejected(db: Session, name: str) -> None:
    with pytest.raises(ValueError, match='Empty analyte name'):
        match_analyte(db, name, None)
    assert _count(db, Analyte) == 0


def test_punctuation_only_names_do_not_merge(db: Session) -> None:
    dot = match_analyte(db, '.', None)
    comma = match_analyte(db, ' , ', None)
    prefix_only = match_analyte(db, 'Аналіз крові:', None)

    assert len({dot.id, comma.id, prefix_only.id}) == 3
    assert all(a.code for a in (dot, comma, prefix_only))
    assert match_analyte(db, '.', None).id == dot.id


def test_single_token_name_equal_to_a_code(db: Session) -> None:
    seed_analytes(db)
    assert match_analyte(db, 'WBC', None).code == 'WBC'
    assert match_analyte(db, ' hgb ', None).code == 'HGB'
    assert match_analyte(db, f'{CYR_HCT}', None).code == 'HCT'


def test_single_token_name_does_not_apply_to_needs_name_codes(db: Session) -> None:
    seed_analytes(db)
    assert match_analyte(db, 'PCT', None).code != 'PCT'
    assert match_analyte(db, 'AI', None).code != 'AI'


def test_multi_word_name_is_not_a_code_token(db: Session) -> None:
    seed_analytes(db)
    assert match_analyte(db, 'WBC test', None).code != 'WBC'
