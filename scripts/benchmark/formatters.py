"""formatting utilities for scientific and International System of Units (SI) values using sciform."""

from typing import Union
from sciform import Formatter
from whenever import TimeDelta


class SIUnitFormatter:
    """formatter for numeric values and durations using sciform with standard SI metric prefixes."""

    def __init__(self, unit: str = "s", precision: int = 3, separator: str = " ") -> None:
        self.unit = unit
        self.precision = precision
        self.separator = separator
        self.engineering_formatter = Formatter(
            exp_mode="engineering",
            exp_format="prefix",
            round_mode="dec_place",
            ndigits=precision,
        )
        self.fixed_point_formatter = Formatter(
            exp_mode="fixed_point",
            round_mode="dec_place",
            ndigits=precision,
        )

    def format(self, value: Union[float, int, TimeDelta]) -> str:
        """format a duration or numeric value into a string with the appropriate SI prefix."""
        numeric_value: float = (
            value.total("seconds") if isinstance(value, TimeDelta) else float(value)
        )
        formatted_text = str(self.engineering_formatter(numeric_value))
        parts = formatted_text.split(" ")
        if len(parts) == 2:
            mantissa, prefix = parts
            prefix = prefix.replace("μ", "µ")
            return f"{mantissa}{self.separator}{prefix}{self.unit}"
        return f"{formatted_text}{self.separator}{self.unit}"

    def format_with_deviation(
        self,
        mean_value: Union[float, int, TimeDelta],
        standard_deviation: Union[float, int, TimeDelta],
    ) -> str:
        """format mean and standard deviation with consistent SI unit prefixes."""
        formatted_mean = self.format(mean_value)
        formatted_deviation = self.format(standard_deviation)
        return f"{formatted_mean} (±{formatted_deviation})"

    def format_value(self, value: Union[float, int, TimeDelta]) -> str:
        """format numeric duration value without unit suffix for inclusion in tables."""
        numeric_value: float = (
            value.total("seconds") if isinstance(value, TimeDelta) else float(value)
        )
        return str(self.fixed_point_formatter(numeric_value))

    def format_value_with_deviation(
        self,
        mean_value: Union[float, int, TimeDelta],
        standard_deviation: Union[float, int, TimeDelta],
        deviation_precision: int = 2,
    ) -> str:
        """format mean and standard deviation without unit suffix for inclusion in tables."""
        formatted_mean = self.format_value(mean_value)
        deviation_numeric: float = (
            standard_deviation.total("seconds")
            if isinstance(standard_deviation, TimeDelta)
            else float(standard_deviation)
        )
        deviation_formatter = Formatter(
            exp_mode="fixed_point",
            round_mode="dec_place",
            ndigits=deviation_precision,
        )
        return f"{formatted_mean} (±{deviation_formatter(deviation_numeric)})"

    def __call__(self, value: Union[float, int, TimeDelta]) -> str:
        """callable shorthand for formatting a value."""
        return self.format(value)
