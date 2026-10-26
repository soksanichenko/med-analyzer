"""Analyte dictionary: name normalisation, seeding and matching.

Different labs print the same analyte under different names, so every result
row is mapped to one ``Analyte`` row, which is what makes dynamics work across
labs. See section 6.4 of the design spec.
"""

import hashlib
import re

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from med_analyzer.analyte_seed import SEED
from med_analyzer.log import get_logger
from med_analyzer.models import Analyte, AnalyteAlias
from med_analyzer.parsing.primitives import _split_top_level, _top_level_groups

logger = get_logger(__name__)

CODE_MAX_LENGTH = 64  # width of ``analytes.code``
TEXT_MAX_LENGTH = 255  # width of ``analytes.name`` and ``analyte_aliases.alias``

# Typographic apostrophes folded into the ASCII one, by code point: left and
# right single quotation marks (U+2018, U+2019), modifier letter apostrophe
# (U+02BC), acute accent (U+00B4) and the backtick (U+0060).
_APOSTROPHES = str.maketrans(dict.fromkeys((0x2018, 0x2019, 0x02BC, 0x00B4, 0x60), "'"))
_BLOOD_TEST_PREFIX = re.compile(r'^аналіз крові\s*:')
# Punctuation that is noise at the ends of a name. Parentheses are kept: a
# trailing ``)`` belongs to the name, e.g. ``нейтрофіли (абс.)``.
_EDGE_CHARS = ' \t\r\n.,;:!?*-–—\'"'

# Cyrillic capitals that look like Latin letters, mapped to their Latin twins.
# Written as code points so that no look-alike glyph appears in the source:
# U+0410 A, U+0412 Ve, U+0415 Ie, U+041A Ka, U+041C Em, U+041D En, U+041E O,
# U+0420 Er, U+0421 Es, U+0422 Te, U+0425 Ha -> A B E K M H O P C T X.
_LOOKALIKES = str.maketrans(
    dict(
        zip(
            (0x0410, 0x0412, 0x0415, 0x041A, 0x041C, 0x041D, 0x041E)
            + (0x0420, 0x0421, 0x0422, 0x0425),
            'ABEKMHOPCTX',
            strict=True,
        )
    )
)
# Codes whose abbreviation alone is not proof of identity (see SeedAnalyte).
_NEEDS_NAME = {item.code for item in SEED if item.abbreviation_needs_name}


def normalize_name(s: str) -> str:
    """Normalise an analyte name for alias comparison.

    Casefolds, unifies typographic apostrophes, collapses whitespace, drops a
    leading ``Аналіз крові:`` prefix and strips punctuation at both ends.

    Args:
        s: Analyte name as printed by a lab.

    Returns:
        The normalised name.
    """
    text = ' '.join(s.translate(_APOSTROPHES).split()).casefold()
    text = _BLOOD_TEST_PREFIX.sub('', text)
    return text.strip(_EDGE_CHARS)


def normalize_abbreviation(s: str) -> str:
    """Normalise an abbreviation to its canonical Latin upper-case form.

    Cyrillic look-alike letters are folded to their Latin twins, so the
    look-alike spelling of ``HCT`` equals the Latin one.

    Args:
        s: Abbreviation as printed by a lab.

    Returns:
        The stripped, upper-cased abbreviation with look-alikes folded.
    """
    return s.strip().upper().translate(_LOOKALIKES)


def seed_analytes(db: Session) -> None:
    """Make sure every seed analyte and alias exists.

    Idempotent and safe to run concurrently: analytes are upserted by code
    (the display name is refreshed), missing aliases are added (the display
    name is deliberately not an alias: a bare ``Лейкоцити`` may be a urine
    test), nothing is deleted. An alias that already belongs to another
    analyte is left alone and reported. The caller owns the transaction; this
    only flushes.

    Args:
        db: Open session.
    """
    stmt = pg_insert(Analyte).values(
        [{'code': item.code, 'name': item.name} for item in SEED]
    )
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=['code'], set_={'name': stmt.excluded.name}
        )
    )
    ids = dict(db.execute(select(Analyte.code, Analyte.id)).all())

    wanted: dict[str, int] = {}
    for item in SEED:
        for spelling in item.aliases:
            wanted[normalize_name(spelling)] = ids[item.code]
    db.execute(
        pg_insert(AnalyteAlias)
        .values([{'analyte_id': i, 'alias': key} for key, i in wanted.items()])
        .on_conflict_do_nothing(index_elements=['alias'])
    )

    owners = dict(
        db.execute(
            select(AnalyteAlias.alias, AnalyteAlias.analyte_id).where(
                AnalyteAlias.alias.in_(wanted)
            )
        ).all()
    )
    foreign = [key for key, analyte_id in wanted.items() if owners[key] != analyte_id]
    if foreign:
        logger.warning(
            'Seed aliases already belong to other analytes: %s', ', '.join(foreign)
        )
    db.flush()


def match_analyte(db: Session, raw_name: str, abbreviation: str | None) -> Analyte:
    """Resolve a result row to an analyte, creating one if nothing matches.

    Order: (1) the given abbreviation equals an analyte code; (2) an
    abbreviation derived from the last parenthesised group of the name (or
    from the name itself when it is a single token) equals an analyte code;
    (3) the normalised name equals a known alias; (4) a new analyte is
    created from the name. A token is only ever trusted if it is an existing
    code, so a stray fragment such as the ``D`` of vitamin D cannot hijack the
    match. For codes that other well-known analytes share (``PCT`` is also
    procalcitonin), an abbreviation hit counts only when the name without its
    trailing parenthesised group is a known spelling of that analyte. Flushes
    but never commits.

    Args:
        db: Open session.
        raw_name: Analyte name as printed in the report.
        abbreviation: Latin abbreviation extracted by the parser, if any.

    Returns:
        The matched or newly created analyte.

    Raises:
        ValueError: If the name is empty and nothing else identifies the row.
    """
    stem = normalize_name(_without_last_group(raw_name))
    if abbreviation:
        analyte = _find_by_code(db, [normalize_abbreviation(abbreviation)], stem)
        if analyte is not None:
            return analyte

    analyte = _find_by_code(db, _derive_abbreviations(raw_name), stem)
    if analyte is not None:
        return analyte

    # A name made only of punctuation normalises to '': keep it distinct
    # rather than merging every such row into one analyte.
    key = (normalize_name(raw_name) or raw_name.strip().casefold())[:TEXT_MAX_LENGTH]
    if not key:
        raise ValueError('Empty analyte name')
    analyte = _find_by_alias(db, key)
    if analyte is not None:
        return analyte
    return _create_analyte(db, raw_name, key)


def _last_group_span(text: str) -> tuple[int, int] | None:
    """Indexes of the ``(`` and ``)`` of the last closed top-level group."""
    span = None
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char == '(':
            if depth == 0:
                start = index
            depth += 1
        elif char == ')' and depth > 0:
            depth -= 1
            if depth == 0:
                span = (start, index)
    return span


def _without_last_group(text: str) -> str:
    """The name with its last top-level parenthesised group cut out."""
    span = _last_group_span(text)
    if span is None:
        return text
    return text[: span[0]] + text[span[1] + 1 :]


def _derive_abbreviations(raw_name: str) -> list[str]:
    """Candidate abbreviations from the last top-level group of a name.

    The group is split on top-level commas and each token is folded, so a
    look-alike group such as that of ``HCT`` yields ``HCT``. A name that is a
    single token without any group (a row named just ``WBC``) is a candidate
    itself.
    """
    groups = _top_level_groups(raw_name)
    if groups:
        tokens = (normalize_abbreviation(t) for t in _split_top_level(groups[-1]))
        return [token for token in tokens if token]
    if len(raw_name.split()) == 1:
        return [normalize_abbreviation(raw_name)]
    return []


def _find_by_code(db: Session, candidates: list[str], stem: str) -> Analyte | None:
    """Return the analyte whose code is the first acceptable candidate."""
    if not candidates:
        return None
    found = {
        analyte.code: analyte
        for analyte in db.scalars(select(Analyte).where(Analyte.code.in_(candidates)))
    }
    for candidate in candidates:
        analyte = found.get(candidate)
        if analyte is not None and _name_confirms(db, analyte, stem):
            return analyte
    return None


def _name_confirms(db: Session, analyte: Analyte, stem: str) -> bool:
    """Whether the abbreviation hit is backed up by the name, where required."""
    if analyte.code not in _NEEDS_NAME:
        return True
    if stem == normalize_name(analyte.name):
        return True
    return (
        db.scalar(
            select(AnalyteAlias.id).where(
                AnalyteAlias.analyte_id == analyte.id, AnalyteAlias.alias == stem
            )
        )
        is not None
    )


def _find_by_alias(db: Session, key: str) -> Analyte | None:
    """Return the analyte that owns a normalised alias, if any."""
    return db.scalar(
        select(Analyte).join(AnalyteAlias).where(AnalyteAlias.alias == key)
    )


def _create_analyte(db: Session, raw_name: str, key: str) -> Analyte:
    """Create an analyte and its alias, or return the one that won a race.

    The insert runs in a SAVEPOINT: if a concurrent request created the same
    alias or code first, only the savepoint is rolled back, the unique
    constraint is hit, and the existing row is returned instead.
    """
    code = _auto_code(db, key)
    try:
        with db.begin_nested():
            analyte = Analyte(code=code, name=raw_name[:TEXT_MAX_LENGTH])
            db.add(analyte)
            db.flush()
            db.add(AnalyteAlias(analyte_id=analyte.id, alias=key))
            db.flush()
    except IntegrityError:
        existing = _find_by_alias(db, key)
        if existing is None:
            raise
        return existing
    logger.info('Created analyte %s from an unmatched name', analyte.id)
    return analyte


def _auto_code(db: Session, key: str) -> str:
    """Code for an auto-created analyte: the normalised name, made unique.

    Names longer than the column, or whose code is already taken by another
    analyte, get a stable hash suffix so that distinct names never collide.
    """
    taken = db.scalar(select(Analyte.id).where(Analyte.code == key)) is not None
    if len(key) <= CODE_MAX_LENGTH and not taken:
        return key
    digest = hashlib.sha1(key.encode(), usedforsecurity=False).hexdigest()[:8]
    return f'{key[: CODE_MAX_LENGTH - len(digest) - 1]}~{digest}'
