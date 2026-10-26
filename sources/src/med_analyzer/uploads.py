"""Upload service: store a PDF, parse it and save its results for one user.

Every query here is filtered by the owning user. The PDF is written to disk
before parsing; any failure afterwards rolls the transaction back and removes
the file again, so a rejected upload leaves neither rows nor files behind.
"""

import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from med_analyzer.analytes import match_analyte
from med_analyzer.config import AppConfig
from med_analyzer.log import get_logger
from med_analyzer.models import AnalysisSet, Result, SetFile, User
from med_analyzer.parsing import parse_pdf
from med_analyzer.parsing.merge import display_value, duplicate_warning, values_differ
from med_analyzer.parsing.primitives import age_in_months
from med_analyzer.parsing.reference import select_reference
from med_analyzer.parsing.types import (
    ParsedReference,
    ParsedResult,
    ParsedSet,
    ParseError,
    ReferenceGroup,
)
from med_analyzer.storage import delete_pdf, save_pdf

logger = get_logger(__name__)

PROFILE_REQUIRED = 'Сначала заполните профиль: пол и дата рождения'
NOT_A_PDF = 'Файл не похож на PDF'
ALREADY_UPLOADED = 'Этот файл уже загружен'
DATE_DIFFERS = 'Дата в файле отличается от даты набора'
NAME_DIFFERS = 'Имя в файле отличается от прежнего'
PDF_MAGIC = b'%PDF-'

# Russian reasons for the fixed English messages of ``ParseError``; matched by
# prefix, in order. Any other message gets the generic reason with the original.
PARSE_ERROR_REASONS = (
    (
        'Unknown report format',
        'Не удалось определить лабораторию: поддерживаются отчёты Lab Service и Синево',
    ),
    ('Ambiguous report format', 'Не удалось однозначно определить лабораторию'),
    (
        'The PDF has no text layer',
        'В PDF нет текстового слоя (сканы не поддерживаются)',
    ),
    ('Cannot read the PDF', 'Не удалось прочитать PDF'),
    ('The PDF has too many', 'В PDF слишком много страниц или текста'),
)


def too_large_reason(config: AppConfig) -> str:
    """Return the user-facing message for a file over the size limit.

    Args:
        config: Application settings (size cap).

    Returns:
        The message in Russian.
    """
    return f'Файл слишком большой (максимум {config.max_upload_mb} МБ)'


def parse_error_reason(error: ParseError) -> str:
    """Translate a parser error into the message shown to the user.

    Args:
        error: The error raised by the parser. Its message is a fixed English
            description with at most page and row numbers, never report text.

    Returns:
        The reason in Russian.
    """
    message = str(error)
    for prefix, reason in PARSE_ERROR_REASONS:
        if message.startswith(prefix):
            return reason
    return f'Не удалось разобрать PDF ({message})'


class UploadRejected(Exception):
    """An upload refused for a reason that can be shown to the user.

    Attributes:
        reason: User-facing explanation in Russian.
        existing_set_id: The set that already holds the same file, if any.
    """

    def __init__(self, reason: str, existing_set_id: int | None = None) -> None:
        """Create the rejection.

        Args:
            reason: User-facing explanation in Russian.
            existing_set_id: The set that already holds the same file, if any.
        """
        super().__init__(reason)
        self.reason = reason
        self.existing_set_id = existing_set_id


def groups_to_json(groups: list[ReferenceGroup]) -> list[dict[str, Any]]:
    """Serialise reference groups for a JSONB column.

    Bounds are stored as strings so no precision is lost to floats.

    Args:
        groups: Parsed reference groups.

    Returns:
        A JSON-serialisable list of dicts.
    """
    return [
        {
            'sex': group.sex,
            'age_from_months': group.age_from_months,
            'age_to_months': group.age_to_months,
            'low': None if group.low is None else str(group.low),
            'high': None if group.high is None else str(group.high),
            'label': group.label,
        }
        for group in groups
    ]


def groups_from_json(data: list[dict[str, Any]]) -> list[ReferenceGroup]:
    """Rebuild reference groups stored by ``groups_to_json``.

    Args:
        data: The stored JSON list.

    Returns:
        The reference groups.
    """
    return [
        ReferenceGroup(
            sex=item['sex'],
            age_from_months=item['age_from_months'],
            age_to_months=item['age_to_months'],
            low=None if item['low'] is None else Decimal(item['low']),
            high=None if item['high'] is None else Decimal(item['high']),
            label=item['label'],
        )
        for item in data
    ]


def store_upload(
    db: Session, user: User, data: bytes, config: AppConfig
) -> tuple[AnalysisSet, bool]:
    """Validate, store and parse an uploaded PDF and save its results.

    A file whose lab and order number match an existing set of the user is
    merged into that set; otherwise a new set is created. Rows and the file
    are committed together or not at all.

    Args:
        db: Open session; committed on success, rolled back on failure.
        user: The uploading user.
        data: The uploaded bytes.
        config: Application settings (storage directory, size cap).

    Returns:
        The set holding the results and whether the file was merged into an
        existing set.

    Raises:
        UploadRejected: The profile is incomplete, the file is not a PDF, is
            too large, was already uploaded by this user or cannot be parsed.
    """
    if user.sex is None or user.birth_date is None:
        raise UploadRejected(PROFILE_REQUIRED)
    if not data.startswith(PDF_MAGIC):
        raise UploadRejected(NOT_A_PDF)
    if len(data) > config.max_upload_mb * 1024 * 1024:
        raise UploadRejected(too_large_reason(config))
    sha256 = hashlib.sha256(data).hexdigest()
    existing = _find_file(db, user, sha256)
    if existing is not None:
        raise UploadRejected(ALREADY_UPLOADED, existing_set_id=existing.set_id)

    _, path = save_pdf(config.storage_dir, user.id, data)
    relative = path.relative_to(config.storage_dir).as_posix()
    try:
        parsed = parse_pdf(path)
        analysis_set, merged = _save_parsed(db, user, parsed, sha256, relative)
        db.commit()
    except Exception as error:
        db.rollback()
        _discard_unreferenced(db, config.storage_dir, relative)
        if isinstance(error, ParseError):
            logger.info('Upload rejected, parse error: %s', error)
            raise UploadRejected(parse_error_reason(error)) from None
        if isinstance(error, IntegrityError):
            # A concurrent upload of the same file won the unique constraint.
            winner = _find_file(db, user, sha256)
            if winner is not None:
                raise UploadRejected(
                    ALREADY_UPLOADED, existing_set_id=winner.set_id
                ) from None
        raise
    logger.info(
        'Upload stored: user_id=%s set_id=%s results=%s merged=%s',
        user.id,
        analysis_set.id,
        len(analysis_set.results),
        merged,
    )
    return analysis_set, merged


def recompute_references(db: Session, user: User, commit: bool = True) -> None:
    """Re-select ``ref_low``/``ref_high`` of all the user's results.

    Uses the stored reference groups with the user's current sex and the age
    at each set's collection date; the PDFs are not re-parsed. Does nothing
    while the profile is incomplete.

    Args:
        db: Open session; committed when done unless ``commit`` is false.
        user: The user whose profile changed.
        commit: Pass ``False`` to leave the transaction to the caller, so the
            profile change and the new ranges are saved together.
    """
    if user.sex is None or user.birth_date is None:
        return
    rows = db.execute(
        select(Result, AnalysisSet.collected_on)
        .join(AnalysisSet, Result.set_id == AnalysisSet.id)
        .where(AnalysisSet.user_id == user.id)
    )
    count = 0
    for result, collected_on in rows:
        reference = ParsedReference(
            raw=result.ref_raw or '', groups=groups_from_json(result.ref_groups)
        )
        result.ref_low, result.ref_high = _select(
            reference, user.sex, user.birth_date, collected_on
        )
        count += 1
    if commit:
        db.commit()
    logger.info('References recomputed: user_id=%s results=%s', user.id, count)


def delete_set(db: Session, user: User, set_id: int, config: AppConfig) -> bool:
    """Delete one of the user's sets with its results and PDF files.

    Args:
        db: Open session; committed when the set is deleted.
        user: The user asking for the deletion.
        set_id: The set to delete.
        config: Application settings (storage directory).

    Returns:
        ``True`` if the set was deleted, ``False`` if the user has no such set
        (nothing is touched then).
    """
    analysis_set = db.scalar(
        select(AnalysisSet).where(
            AnalysisSet.id == set_id, AnalysisSet.user_id == user.id
        )
    )
    if analysis_set is None:
        return False
    file_paths = [set_file.file_path for set_file in analysis_set.files]
    db.delete(analysis_set)
    db.commit()
    # Files go only after the commit: a failed delete must not lose PDFs
    # that rows still point to.
    for file_path in file_paths:
        try:
            delete_pdf(config.storage_dir, config.storage_dir / file_path)
        except (OSError, ValueError) as error:
            logger.warning(
                'Cannot remove a PDF of deleted set_id=%s: %s',
                set_id,
                type(error).__name__,
            )
    logger.info('Set deleted: user_id=%s set_id=%s', user.id, set_id)
    return True


def _find_file(db: Session, user: User, sha256: str) -> SetFile | None:
    return db.scalar(
        select(SetFile).where(SetFile.user_id == user.id, SetFile.sha256 == sha256)
    )


def _discard_unreferenced(db: Session, storage_dir: Path, file_path: str) -> None:
    """Remove a freshly saved file unless a committed row refers to it."""
    try:
        referenced = db.scalar(
            select(SetFile.id).where(SetFile.file_path == file_path).limit(1)
        )
    except SQLAlchemyError:
        # Without the answer, keeping a possibly orphaned file is safer than
        # deleting one that another upload owns.
        db.rollback()
        logger.warning('Cannot check whether a failed upload file is referenced')
        return
    if referenced is None:
        try:
            delete_pdf(storage_dir, storage_dir / file_path)
        except OSError as error:
            # An orphaned file is harmless; masking the real failure is not.
            logger.warning(
                'Cannot remove a failed upload file: %s', type(error).__name__
            )


def _select(
    reference: ParsedReference, sex: str, birth_date: date, collected_on: date
) -> tuple[Decimal | None, Decimal | None]:
    # A negative age (collection before birth) selects nothing, not an error.
    return select_reference(reference, sex, age_in_months(birth_date, collected_on))


def _save_parsed(
    db: Session, user: User, parsed: ParsedSet, sha256: str, file_path: str
) -> tuple[AnalysisSet, bool]:
    """Add the parsed results to a matching or new set; flushes, never commits."""
    warnings = list(parsed.warnings)
    analysis_set = None
    if parsed.order_number is not None:
        analysis_set = db.scalar(
            select(AnalysisSet).where(
                AnalysisSet.user_id == user.id,
                AnalysisSet.lab == parsed.lab,
                AnalysisSet.order_number == parsed.order_number,
            )
        )
    merged = analysis_set is not None
    if analysis_set is None:
        analysis_set = AnalysisSet(
            user_id=user.id,
            lab=parsed.lab,
            collected_on=parsed.collected_on,
            order_number=parsed.order_number,
            patient_name=parsed.patient_name,
            warnings=[],
        )
        db.add(analysis_set)
    elif parsed.collected_on != analysis_set.collected_on:
        warnings.append(DATE_DIFFERS)

    if parsed.patient_name is not None:
        if user.last_patient_name is None:
            user.last_patient_name = parsed.patient_name
        elif not _same_name(user.last_patient_name, parsed.patient_name):
            warnings.append(NAME_DIFFERS)
        elif len(parsed.patient_name) > len(user.last_patient_name):
            # The same person in a fuller form (full name vs initials).
            user.last_patient_name = parsed.patient_name

    db.add(
        SetFile(
            analysis_set=analysis_set,
            user_id=user.id,
            sha256=sha256,
            file_path=file_path,
        )
    )
    warnings.extend(_add_results(db, user, analysis_set, parsed.results))
    # A new list, so SQLAlchemy sees the JSONB change.
    analysis_set.warnings = [*analysis_set.warnings, *warnings]
    db.flush()
    return analysis_set, merged


def _add_results(
    db: Session, user: User, analysis_set: AnalysisSet, rows: list[ParsedResult]
) -> list[str]:
    """Add rows for analytes the set does not have yet; return the warnings."""
    warnings: list[str] = []
    # Results already in the set (on merge) win over the new file's rows.
    kept: dict[int, Result] = {r.analyte_id: r for r in analysis_set.results}
    from_this_file: set[int] = set()
    for row in rows:
        analyte = match_analyte(db, row.raw_name, row.abbreviation)
        first = kept.get(analyte.id)
        if first is not None:
            if values_differ(first.value, first.value_text, row.value, row.value_text):
                warnings.append(
                    duplicate_warning(
                        row.raw_name,
                        display_value(first.value, first.value_text),
                        display_value(row.value, row.value_text),
                    )
                )
            elif analyte.id in from_this_file:
                # Two spellings in one report resolving to one analyte hint at
                # a matching problem even when the values agree.
                warnings.append(
                    f'Повтор показателя «{row.raw_name}» пропущен: '
                    f'тот же показатель, что и «{first.raw_name}»'
                )
            continue
        ref_low, ref_high = _select(
            row.reference, user.sex, user.birth_date, analysis_set.collected_on
        )
        result = Result(
            analysis_set=analysis_set,
            analyte_id=analyte.id,
            raw_name=row.raw_name,
            value=row.value,
            value_text=row.value_text,
            unit=row.unit,
            flag=row.flag,
            ref_raw=row.reference.raw,
            ref_groups=groups_to_json(row.reference.groups),
            ref_low=ref_low,
            ref_high=ref_high,
        )
        db.add(result)
        kept[analyte.id] = result
        from_this_file.add(analyte.id)
    return warnings


def _same_name(first: str, second: str) -> bool:
    """Tell whether two printed names can be the same person.

    Labs differ in case, spacing and form: one prints ``Surname I. P.``, the
    other the full name. The surnames must match; each following token pair
    only needs the same first letter, so initials match the full name.
    """
    first_tokens = first.casefold().replace('.', ' ').split()
    second_tokens = second.casefold().replace('.', ' ').split()
    if not first_tokens or not second_tokens:
        return first_tokens == second_tokens
    if first_tokens[0] != second_tokens[0]:
        return False
    return all(a[0] == b[0] for a, b in zip(first_tokens[1:], second_tokens[1:]))
