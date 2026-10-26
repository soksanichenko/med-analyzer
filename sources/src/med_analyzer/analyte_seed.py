"""Seed data for the analyte dictionary.

``code`` is the canonical Latin abbreviation. ``name`` is the display name; it
is NOT registered as an alias, because a bare ``Лейкоцити`` may be a urine
test. ``aliases`` are the exact spellings printed by the labs for rows that
carry no usable abbreviation; they are normalised with ``normalize_name`` when
stored, so they may be written as printed.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SeedAnalyte:
    """One analyte of the seed dictionary.

    Attributes:
        code: Canonical Latin abbreviation, unique.
        name: Display name (Ukrainian).
        aliases: Name spellings that resolve to this analyte.
        abbreviation_needs_name: Set for codes that other well-known analytes
            share (``PCT`` is also procalcitonin, ``AI`` is a generic token).
            An abbreviation hit then counts only if the name without its
            trailing parenthesised group is the display name or an alias.
    """

    code: str
    name: str
    aliases: tuple[str, ...] = ()
    abbreviation_needs_name: bool = False


SEED: list[SeedAnalyte] = [
    # Complete blood count.
    SeedAnalyte('WBC', 'Лейкоцити'),
    SeedAnalyte('RBC', 'Еритроцити'),
    SeedAnalyte('HGB', 'Гемоглобін'),
    SeedAnalyte('HCT', 'Гематокрит'),
    SeedAnalyte('MCV', "Середній об'єм еритроцита"),
    SeedAnalyte('MCH', 'Середній вміст гемоглобіну в еритроциті'),
    SeedAnalyte('MCHC', 'Середня концентрація гемоглобіну в еритроциті'),
    SeedAnalyte('PLT', 'Тромбоцити'),
    SeedAnalyte('RDW-SD', 'Ширина розподілу еритроцитів (стандартне відхилення)'),
    SeedAnalyte('RDW-CV', 'Ширина розподілу еритроцитів (коефіцієнт варіації)'),
    SeedAnalyte('PDW', 'Ширина розподілу тромбоцитів'),
    SeedAnalyte('MPV', "Середній об'єм тромбоцитів"),
    # PCT is also procalcitonin: trust the abbreviation only with the stem.
    SeedAnalyte('PCT', 'Тромбокрит', abbreviation_needs_name=True),
    SeedAnalyte(
        'P-LCR',
        'Відношення великих тромбоцитів до загальної кількості тромбоцитів',
        (
            (
                'Відношення кількості великих тромбоцитів до загальної '
                'кількості тромбоцитів (P-LCR)'
            ),
        ),
    ),
    # Leukocyte differential: percentages, then absolute counts. Synevo prints
    # no abbreviation for these, so the names are the only handle.
    SeedAnalyte('NEUT%', 'Нейтрофіли, %', ('Нейтрофіли (на 100 лейкоцитів)',)),
    SeedAnalyte('LYM%', 'Лімфоцити, %', ('Лімфоцити (на 100 лейкоцитів)',)),
    SeedAnalyte('MONO%', 'Моноцити, %', ('Моноцити (на 100 лейкоцитів)',)),
    SeedAnalyte('EO%', 'Еозинофіли, %', ('Еозинофіли (на 100 лейкоцитів)',)),
    SeedAnalyte('BASO%', 'Базофіли, %', ('Базофіли (на 100 лейкоцитів)',)),
    SeedAnalyte(
        'IMG%',
        'Незрілі гранулоцити, %',
        ('Незрілі гранулоцити (на 100 лейкоцитів)',),
    ),
    SeedAnalyte('NEUT#', 'Нейтрофіли, абс.', ('Нейтрофіли (абс.)',)),
    SeedAnalyte('LYM#', 'Лімфоцити, абс.', ('Лімфоцити (абс.)',)),
    SeedAnalyte('MONO#', 'Моноцити, абс.', ('Моноцити (абс.)',)),
    SeedAnalyte('EO#', 'Еозинофіли, абс.', ('Еозинофіли (абс.)',)),
    SeedAnalyte('BASO#', 'Базофіли, абс.', ('Базофіли (абс.)',)),
    SeedAnalyte('IMG#', 'Незрілі гранулоцити, абс.', ('Незрілі гранулоцити (абс.)',)),
    SeedAnalyte(
        'ESR',
        'Швидкість осідання еритроцитів (ШОЕ)',
        (
            'Швидкість осідання еритроцитів за Вестергреном',
            'Аналіз крові: Швидкість осідання еритроцитів (ШОЕ, ESR)',
        ),
    ),
    # Chemistry, vitamins and hormones present in the Synevo samples.
    SeedAnalyte('CREA', 'Креатинін', ('Креатинін',)),
    SeedAnalyte('GLU', 'Глюкоза', ('Глюкоза (сироватка)',)),
    SeedAnalyte('B12', 'Вітамін B12', ('Ціанокобаламін (вітамін B12)',)),
    SeedAnalyte('FERR', 'Феритин', ('Феритин',)),
    SeedAnalyte('FOLATE', 'Фолієва кислота', ('Фолієва кислота (сироватка)',)),
    # The printed abbreviation of this row is not a safe key (see the matcher),
    # so the name alias is what resolves it.
    SeedAnalyte(
        'VITD',
        'Вітамін D (25-гідроксивітамін D)',
        ('Вітамін Д (25-гідроксивітамін D, 25- (OH)D)',),
    ),
    SeedAnalyte('CHOL', 'Холестерин', ('Холестерин',)),
    # Not TG: that is also the usual abbreviation of thyroglobulin (Tg).
    SeedAnalyte('TRIG', 'Тригліцериди', ('Тригліцериди',)),
    SeedAnalyte(
        'HDL',
        'Ліпопротеїди високої щільності',
        ('Ліпопротеїди високої щільності (ЛПВЩ, HDL)',),
    ),
    SeedAnalyte(
        'LDL',
        'Ліпопротеїди низької щільності',
        ('Ліпопротеїди низької щільності (ЛПНЩ, LDL)',),
    ),
    SeedAnalyte(
        'VLDL',
        'Ліпопротеїди дуже низької щільності',
        ('Ліпопротеїди дуже низької щільності (VLDL)',),
    ),
    SeedAnalyte(
        'NONHDL',
        'Холестерин не-ЛПВЩ',
        ('Холестерин не-Ліпопротеїнів високої щільності (ХС не-ЛПВЩ)',),
    ),
    SeedAnalyte(
        'AI',
        'Коефіцієнт атерогенності',
        ('Коефіцієнт атерогенності (КА)',),
        abbreviation_needs_name=True,
    ),
    SeedAnalyte(
        'TSH',
        'Тиреотропний гормон',
        ('Тиреотропний гормон. ВИСОКОЧУТЛИВИЙ, БІОТИН-НЕЗАЛЕЖНИЙ',),
    ),
    SeedAnalyte('FT4', 'Тироксин вільний', ('Тироксин вільний (T4 вільний)',)),
    SeedAnalyte(
        'FT3', 'Трийодтиронін вільний', ('Трийодтиронін вільний (T3 вільний)',)
    ),
    SeedAnalyte('AST', 'Аспартатамінотрансфераза'),
    SeedAnalyte('ALT', 'Аланінамінотрансфераза'),
    SeedAnalyte(
        'TPO-AB',
        'Антитіла до тиреопероксидази',
        ('Пероксидаза щитоподібної залози, антитіла (АТПО)',),
    ),
]
