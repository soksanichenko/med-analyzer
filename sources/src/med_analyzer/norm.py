"""Norm math: position of a result inside its reference range.

Pure functions over ``Decimal`` values; no database or web dependencies.
"""

from decimal import Decimal


def percent_of_norm(
    value: Decimal | None, low: Decimal | None, high: Decimal | None
) -> float | None:
    """Return the position of ``value`` within the reference range, in percent.

    ``0`` is the lower bound and ``100`` is the upper bound:
    ``(value - low) / (high - low) * 100``. The arithmetic is done in
    ``Decimal`` and converted to ``float`` only at the end.

    Conventions:
        * ``value is None`` gives ``None``.
        * Both bounds: ``high <= low`` is an invalid range and gives ``None``.
        * Only ``high`` ("до X", ``<X``, ``<=X``): ``low`` is taken as ``0``,
          so the result is ``value / high * 100``; ``high <= 0`` gives ``None``.
        * Only ``low`` or neither bound: ``None``, since without an upper bound
          there is no percentage.
        * Results below 0 or above 100 are returned as is; clamping is left
          to the chart axis.

    Args:
        value: Measured result.
        low: Lower reference bound, if any.
        high: Upper reference bound, if any.

    Returns:
        The percentage, or ``None`` when it is not defined.
    """
    if value is None or high is None:
        return None
    if low is None:
        # An upper-bound-only norm starts at zero by convention.
        low = Decimal(0)
        if high <= 0:
            return None
    elif high <= low:
        return None
    return float((value - low) / (high - low) * 100)


def is_out_of_range(
    value: Decimal | None, low: Decimal | None, high: Decimal | None
) -> bool | None:
    """Tell whether ``value`` lies outside the reference range.

    Bounds are inclusive: a value equal to a bound is in range.

    Conventions:
        * ``value is None`` or both bounds ``None`` gives ``None``.
        * Only ``low``: out of range when ``value < low``.
        * Only ``high``: out of range when ``value > high``.
        * Both: out of range when either check holds.

    Args:
        value: Measured result.
        low: Lower reference bound, if any.
        high: Upper reference bound, if any.

    Returns:
        ``True`` if out of range, ``False`` if in range, ``None`` when it
        cannot be determined.
    """
    if value is None or (low is None and high is None):
        return None
    below = low is not None and value < low
    above = high is not None and value > high
    return below or above
