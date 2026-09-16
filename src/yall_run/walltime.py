"""Portable, finite wall-time requests, stored as whole seconds."""
from __future__ import annotations

import re


# Keep values representable by schedulers with signed 32-bit second counters.
# Site/queue limits can be much lower and remain the scheduler's responsibility.
MAX_WALLTIME_SECONDS = 2**31 - 1
_UNITS = re.compile(
    r"(?:(?P<days>[0-9]+)d)?(?:(?P<hours>[0-9]+)h)?"
    r"(?:(?P<minutes>[0-9]+)m)?(?:(?P<seconds>[0-9]+)s)?",
    re.IGNORECASE,
)
_CLOCK = re.compile(r"(?:([0-9]+)-)?([0-9]+):([0-5][0-9]):([0-5][0-9])")


def validate_walltime(value: int | None) -> int | None:
    """Validate the normalized model value; None means site/default policy."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("wall time must be an integer number of seconds")
    if not 1 <= value <= MAX_WALLTIME_SECONDS:
        raise ValueError(
            f"wall time must be between 1 and {MAX_WALLTIME_SECONDS} seconds"
        )
    return value


def parse_walltime(value: str) -> int:
    """Accept seconds, ordered d/h/m/s units, or [D-]HH:MM:SS.

    Bare integers always mean seconds, never backend-dependent minutes.
    Zero/unlimited, fractional seconds, and ambiguous MM:SS are rejected.
    """
    if not isinstance(value, str):
        raise ValueError("wall time requires a duration string")
    text = value.strip()
    # Reject huge literals before int() and before backend formatting.
    if not text or len(text) > 64:
        raise ValueError("invalid wall time; use e.g. 7200s, 2h, 1h30m, or 02:00:00")
    if re.fullmatch(r"[0-9]+", text):
        seconds = int(text)
    else:
        clock = _CLOCK.fullmatch(text)
        units = _UNITS.fullmatch(text)
        if clock:
            days, hours, minutes, secs = clock.groups()
            if days is not None and int(hours) >= 24:
                raise ValueError("wall time D-HH:MM:SS requires HH below 24")
            seconds = (
                int(days or 0) * 86400 + int(hours) * 3600
                + int(minutes) * 60 + int(secs)
            )
        elif units:
            values = units.groupdict()
            seconds = sum(
                int(values[name] or 0) * scale
                for name, scale in (
                    ("days", 86400), ("hours", 3600),
                    ("minutes", 60), ("seconds", 1),
                )
            )
        else:
            raise ValueError(
                "invalid wall time; use e.g. 7200s, 2h, 1h30m, or 02:00:00"
            )
    validate_walltime(seconds)
    return seconds


def effective_walltime(task_seconds: int | None, default_seconds: int | None) -> int | None:
    """Resolve a task override against the campaign's optional default."""
    return validate_walltime(
        task_seconds if task_seconds is not None else default_seconds
    )


def format_walltime(seconds: int, *, slurm: bool = False) -> str:
    """Format total hours for PBS/display, or Slurm's day-hour notation.

    Preserve requested seconds. Slurm itself rounds up to minute resolution.
    """
    if validate_walltime(seconds) is None:
        raise ValueError("cannot format an unspecified wall time")
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    prefix = ""
    if slurm and hours >= 24:
        days, hours = divmod(hours, 24)
        prefix = f"{days}-"
    return f"{prefix}{hours:02d}:{minutes:02d}:{secs:02d}"
