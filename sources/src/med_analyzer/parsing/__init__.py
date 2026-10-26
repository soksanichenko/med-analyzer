"""PDF parsing pipeline: format detection and the per-lab parsers.

Error messages never carry text from the report, only fixed descriptions, so
they are safe to show to a user or write to a log.
"""

from pathlib import Path

from med_analyzer.log import get_logger
from med_analyzer.parsing.lab_service import parse_lab_service
from med_analyzer.parsing.synevo import parse_synevo
from med_analyzer.parsing.types import Page, ParsedSet, ParseError
from med_analyzer.parsing.words import pdf_to_pages

__all__ = ['detect_lab', 'parse_pdf']

logger = get_logger(__name__)

# Punctuation printed around the marker, e.g. ``(Synevo)``.
_STRIP_CHARS = '()[]«»"\'.,:;'
_SYNEVO_MARKERS = frozenset({'Synevo', 'Сінево'})
_LAB_SERVICE_TITLE = ('Результати', 'досліджень')
_LAB_SERVICE_HEADER = ('Найменування', 'Референтні')


def _is_synevo(texts: list[str]) -> bool:
    return any(text.strip(_STRIP_CHARS) in _SYNEVO_MARKERS for text in texts)


def _is_lab_service(texts: list[str]) -> bool:
    # The ``№`` sign is printed either alone or glued to the number.
    has_title = any(
        texts[i : i + 2] == list(_LAB_SERVICE_TITLE) and texts[i + 2].startswith('№')
        for i in range(len(texts) - 2)
    )
    return has_title and all(word in texts for word in _LAB_SERVICE_HEADER)


def detect_lab(pages: list[Page]) -> str:
    """Tell which laboratory issued a report from the first page.

    Args:
        pages: Pages of words as returned by ``pdf_to_pages``.

    Returns:
        ``'synevo'`` or ``'lab_service'``.

    Raises:
        ParseError: If the first page matches no known format or matches
            more than one.
    """
    texts = [word.text for word in pages[0]] if pages else []
    synevo = _is_synevo(texts)
    lab_service = _is_lab_service(texts)
    if synevo and lab_service:
        raise ParseError('Ambiguous report format')
    if synevo:
        return 'synevo'
    if lab_service:
        return 'lab_service'
    raise ParseError('Unknown report format')


def parse_pdf(path: Path) -> ParsedSet:
    """Read a lab report PDF and parse its results.

    Args:
        path: Path of the PDF file.

    Returns:
        The parsed report.

    Raises:
        ParseError: If the file cannot be read, has no text layer, is in an
            unknown format or does not fit the layout of its parser.
    """
    try:
        pages = pdf_to_pages(path)
    except ParseError:
        raise
    except Exception as error:
        # pdfminer messages may quote file content; log only the type.
        logger.warning('Cannot read a PDF: %s', type(error).__name__)
        raise ParseError('Cannot read the PDF') from None
    if not any(pages):
        raise ParseError(
            'The PDF has no text layer (scanned documents are not supported)'
        )
    if detect_lab(pages) == 'synevo':
        return parse_synevo(pages)
    return parse_lab_service(pages)
