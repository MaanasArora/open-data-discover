"""Numbers and dates: how densely a column covers the values around a given value.

A column holding every id from 1 to 800, or every day of 2023, contains any value in
that range whatever it means, so sharing one of them is no evidence of a join. For
columns whose values are numbers or dates this is measurable: the values sit on a
lattice (steps of 1, 0.01, a day, a week, ...), and the column's density at a value is
the share of the lattice points around it, within ``DENSITY_WINDOW`` steps on each
side, that the column fills. For the column's own values, the value itself is left out.

The lattice is the one that at least ``MIN_SHARE`` of the values sit on, with the largest
step: a daily series with a few odd timestamps is still daily, and a regular series
(every Tuesday, multiples of 100) is dense on its own lattice. Daily dates that keep to
some days of the week (permits issued on weekdays) only count those days as lattice
points. It takes ``MIN_VALUES`` values to tell; columns with fewer, or with numbers too
fine for a lattice (coordinates, measurements), have density 0. The window is clipped
to the column's range.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

DENSITY_WINDOW = 50  # lattice steps on each side of a value
MIN_SHARE = 0.95  # share of values that must parse, and must sit on the lattice
MIN_VALUES = 10  # fewer values do not show which lattice they are on
MIN_WEEK_VALUES = 50  # daily dates needed to tell which days of the week they keep to
MAX_DECIMALS = 9  # numbers with more decimals get no lattice
DAY = 86_400  # seconds
# Tried in order after ISO 8601, on upper-cased values (strptime ignores case).
DATE_FORMATS = ["%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%d-%b-%y",
                "%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M:%S %p"]
EPOCH = pd.Timestamp("1970-01-01", tz="UTC")


@dataclass
class Order:
    """How a column's values sit on a line."""
    kind: str  # "number" or "date"
    positions: np.ndarray  # per value: the number, or seconds since 1970; NaN if it did not parse
    scale: float = 1.0  # lattice ticks per unit of position
    grid: np.ndarray | None = None  # sorted distinct ticks; None when there is no usable lattice
    step: int = 1  # lattice step, in ticks
    origin: int = 0  # a tick on the lattice
    # lattice points kept, by lattice index mod 7 (only daily dates drop some days of the week)
    weekdays: np.ndarray = field(default_factory=lambda: np.ones(7, dtype=bool))

    def density(self, positions: np.ndarray, own: bool = False) -> np.ndarray:
        """This column's density at each position (0 where it cannot say).

        With ``own``, the positions are the column's own values and each is left out of
        its window. Otherwise positions off the lattice have density 0.
        """
        result = np.zeros(len(positions))
        if self.grid is None or not len(positions):
            return result
        scaled = positions * self.scale
        ticks = np.round(scaled)
        inside = np.isfinite(ticks) & (ticks >= self.grid[0]) & (ticks <= self.grid[-1])
        whole = np.abs(scaled[inside] - ticks[inside]) < 1e-6  # on a tick at all
        x = ticks[inside].astype(np.int64)
        half = min(DENSITY_WINDOW * self.step, int(self.grid[-1] - self.grid[0]))
        lo = np.where(x - self.grid[0] > half, x - half, self.grid[0])
        hi = np.where(self.grid[-1] - x > half, x + half, self.grid[-1])
        count = np.searchsorted(self.grid, hi, "right") - np.searchsorted(self.grid, lo, "left")
        first, last = -((self.origin - lo) // self.step), (hi - self.origin) // self.step  # lattice indices
        points = self.points_before(last + 1) - self.points_before(first)
        on_lattice = whole & ((x - self.origin) % self.step == 0) & self.weekdays[((x - self.origin) // self.step) % 7]
        if own:  # leave the value itself out
            others = points - on_lattice
            dens = np.where(others > 0, (count - 1) / np.maximum(others, 1), 0.0)
        else:
            dens = np.where(on_lattice & (points > 0), count / np.maximum(points, 1), 0.0)
        result[inside] = np.clip(dens, 0.0, 1.0)
        return result

    def points_before(self, index: np.ndarray) -> np.ndarray:
        """Number of kept lattice points with index below ``index`` (counting from 0)."""
        kept = np.concatenate([[0], np.cumsum(self.weekdays)])
        return (index // 7) * kept[7] + kept[index % 7]


def order(values) -> Order | None:
    """The values' order if nearly all of them are numbers or dates, else ``None``."""
    values = pd.Series(values, dtype=str)
    if values.empty or values.iloc[:200].str.contains(r"\d").mean() < MIN_SHARE:
        return None
    found = numbers(values) or dates(values)
    if found is not None and found.grid is not None:
        if len(found.grid) < MIN_VALUES:
            found.grid = None
        else:
            found.step, found.origin = lattice(found.grid)
            if found.kind == "date" and found.step == DAY and len(found.grid) >= MIN_WEEK_VALUES:
                found.weekdays = weekdays(((found.grid - found.origin) // DAY) % 7)
    return found


def numbers(values: pd.Series) -> Order | None:
    """Numbers, on a grid of 10**-decimals when they have few enough decimals."""
    x = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float, copy=True)
    parsed = np.isfinite(x)
    if parsed.mean() < MIN_SHARE:
        return None
    x[~parsed] = np.nan
    result = Order("number", x)
    text = values[parsed]
    decimals = int(text.str.partition(".")[2].str.len().max())
    if decimals <= MAX_DECIMALS and not text.str.contains("e", regex=False).any():
        result.scale = 10.0 ** decimals
        ticks = np.round(x[parsed] * result.scale)
        if np.abs(ticks).max() < 2.0 ** 62:
            result.grid = np.unique(ticks.astype(np.int64))
    return result


def dates(values: pd.Series) -> Order | None:
    """Dates and times, in the first format that fits (seconds since 1970)."""
    values = values.str.upper()  # profiles are lower-cased; ISO 8601 wants "T" and "Z"
    for fmt in ["ISO8601", *DATE_FORMATS]:
        sample = pd.to_datetime(values.iloc[:200], format=fmt, errors="coerce", utc=True)
        if sample.notna().mean() < MIN_SHARE:
            continue
        parsed_dates = pd.to_datetime(values, format=fmt, errors="coerce", utc=True)
        parsed = parsed_dates.notna().to_numpy()
        if parsed.mean() < MIN_SHARE:
            continue
        seconds = np.full(len(values), np.nan)
        seconds[parsed] = ((parsed_dates[parsed] - EPOCH) // pd.Timedelta(1, "s")).to_numpy(dtype=float)
        return Order("date", seconds, grid=np.unique(seconds[parsed].astype(np.int64)))
    return None


def lattice(grid: np.ndarray) -> tuple[int, int]:
    """``(step, origin)``: the lattice with the largest step that ``MIN_SHARE`` of the
    values sit on. Candidates are the most common gaps and their common divisors; the
    fallback, the divisor of all gaps, holds every value."""
    gaps = np.diff(grid)
    whole = int(np.gcd.reduce(gaps))
    common = pd.Series(gaps).value_counts().index[:5].to_numpy(dtype=np.int64)
    for step in sorted({int(np.gcd(a, b)) for a in common for b in common}, reverse=True):
        if step <= whole:
            break
        residues = pd.Series(grid % step).value_counts()
        if residues.iloc[0] >= MIN_SHARE * len(grid):
            return step, int(residues.index[0])
    return whole, int(grid[0] % whole)


def weekdays(days: np.ndarray) -> np.ndarray:
    """The fewest days of the week (as lattice index mod 7) holding ``MIN_SHARE`` of the dates."""
    counts = np.bincount(days, minlength=7)
    by_count = np.argsort(-counts, kind="stable")
    needed = np.searchsorted(np.cumsum(counts[by_count]), MIN_SHARE * len(days)) + 1
    kept = np.zeros(7, dtype=bool)
    kept[by_count[:needed]] = True
    return kept
