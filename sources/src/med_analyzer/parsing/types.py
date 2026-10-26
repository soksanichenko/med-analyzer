"""Data structures shared by the PDF parsing pipeline."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Word:
    """A single word extracted from a PDF page, with its bounding box.

    Attributes:
        text: The word text.
        x0: Left edge.
        x1: Right edge.
        top: Top edge.
        bottom: Bottom edge.
    """

    text: str
    x0: float
    x1: float
    top: float
    bottom: float


Page = list[Word]


@dataclass(frozen=True)
class ReferenceGroup:
    """One reference range, optionally restricted by sex and age band.

    The age band is half-open: ``[age_from_months, age_to_months)``.

    Attributes:
        sex: Sex the range applies to, or ``None`` for any.
        age_from_months: Inclusive lower age bound in months, if any.
        age_to_months: Exclusive upper age bound in months, if any.
        low: Lower limit of the range, if any.
        high: Upper limit of the range, if any.
        label: Human-readable label of the group as printed in the PDF.
    """

    sex: str | None
    age_from_months: int | None
    age_to_months: int | None
    low: Decimal | None
    high: Decimal | None
    label: str


@dataclass(frozen=True)
class ParsedReference:
    """The reference column of a result: raw text plus parsed groups.

    Attributes:
        raw: The reference text exactly as extracted.
        groups: Reference ranges parsed from the text.
    """

    raw: str
    groups: list[ReferenceGroup]


@dataclass
class ParsedResult:
    """A single analyte row parsed from a lab report.

    Attributes:
        raw_name: Analyte name as printed in the PDF.
        abbreviation: Latin abbreviation extracted from the name, if any.
        value: Numeric value, if the result is numeric.
        value_text: Textual value for non-numeric results.
        unit: Normalised unit, if any.
        flag: Out-of-range flag printed next to the value, if any.
        reference: The parsed reference column.
    """

    raw_name: str
    abbreviation: str | None
    value: Decimal | None
    value_text: str | None
    unit: str | None
    flag: str | None
    reference: ParsedReference


@dataclass
class ParsedSet:
    """All results parsed from one report.

    Attributes:
        lab: Laboratory identifier.
        collected_on: Date the sample was collected.
        order_number: Lab order number, if present.
        patient_name: Patient name as printed, if present.
        results: Parsed analyte rows.
        warnings: Non-fatal problems met while parsing.
    """

    lab: str
    collected_on: date
    order_number: str | None
    patient_name: str | None
    results: list[ParsedResult]
    warnings: list[str]


class ParseError(Exception):
    """Raised when a report cannot be parsed."""
