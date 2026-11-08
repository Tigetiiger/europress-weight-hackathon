"""Hourly local-time timeline, Finnish public holidays and truck service hours."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd


def easter_sunday(year: int) -> dt.date:
    """Gregorian Easter Sunday (anonymous Meeus/Jones/Butcher algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month, day = divmod(h + ell - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def _first_saturday_from(year: int, month: int, day: int) -> dt.date:
    start = dt.date(year, month, day)
    return start + dt.timedelta(days=(5 - start.weekday()) % 7)


def finnish_holidays(years: Iterable[int], hcfg: Mapping[str, Any]) -> set[dt.date]:
    """Finnish public holidays for the given years (rules in config.calendar)."""
    out: set[dt.date] = set()
    for year in years:
        for month_day in hcfg["fixed"]:
            month, day = (int(x) for x in month_day.split("-"))
            out.add(dt.date(year, month, day))
        easter = easter_sunday(year)
        out.update(easter + dt.timedelta(days=int(o)) for o in hcfg["easter_offsets"])
        if hcfg.get("midsummer", True):
            midsummer_day = _first_saturday_from(year, 6, 20)
            out.update({midsummer_day, midsummer_day - dt.timedelta(days=1)})
        if hcfg.get("all_saints", True):
            out.add(_first_saturday_from(year, 10, 31))
    return out


@dataclass(frozen=True)
class Timeline:
    """Hourly simulation clock (burn-in included) in local time."""

    ts: pd.DatetimeIndex      # (T,) tz-aware local hour starts
    hour: np.ndarray          # (T,) local hour of day
    dow: np.ndarray           # (T,) 0 = Monday
    month: np.ndarray         # (T,) 1..12
    day_idx: np.ndarray       # (T,) index into `dates`
    dates: pd.DatetimeIndex   # (D,) local calendar dates (naive)
    doy: np.ndarray           # (D,) day of year
    holiday: np.ndarray       # (D,) bool
    day_start: np.ndarray     # (D,) first hour index of each day
    day_len: np.ndarray       # (D,) hours in each day (23/25 on DST days)
    main_start: int           # first reported hour index
    main_day_start: int       # first reported day index

    @property
    def n_hours(self) -> int:
        return len(self.ts)

    @property
    def n_days(self) -> int:
        return len(self.dates)

    @property
    def n_main_days(self) -> int:
        return self.n_days - self.main_day_start


def build_timeline(cfg: Mapping[str, Any]) -> Timeline:
    run = cfg["run"]
    tz = run["timezone"]
    start_date = pd.Timestamp(run["start"]).normalize()
    sim_start = start_date - pd.Timedelta(days=int(run["burn_in_days"]))
    end_date = start_date + pd.Timedelta(days=int(run["days"]))
    ts = pd.date_range(sim_start.tz_localize(tz), end_date.tz_localize(tz), freq="h", inclusive="left")
    ts = ts.as_unit("ns")  # pandas >= 3 defaults to us; asi8 is used as ns downstream
    local = ts.tz_localize(None)
    day_idx = np.asarray((local.normalize() - sim_start).days, dtype=np.int64)
    dates = pd.date_range(sim_start, end_date, freq="D", inclusive="left")
    holidays = finnish_holidays(range(sim_start.year, end_date.year + 1), cfg["calendar"]["holidays"])
    day_len = np.bincount(day_idx, minlength=len(dates))
    day_start = np.concatenate([[0], np.cumsum(day_len)[:-1]])
    return Timeline(
        ts=ts,
        hour=local.hour.to_numpy().astype(np.int64),
        dow=local.day_of_week.to_numpy().astype(np.int64),
        month=local.month.to_numpy().astype(np.int64),
        day_idx=day_idx,
        dates=dates,
        doy=dates.day_of_year.to_numpy().astype(np.float64),
        holiday=np.array([d.date() in holidays for d in dates]),
        day_start=day_start,
        day_len=day_len,
        main_start=int(np.searchsorted(ts.asi8, start_date.tz_localize(tz).value)),
        main_day_start=int((start_date - sim_start).days),
    )


def truck_allowed(tl: Timeline, ocfg: Mapping[str, Any]) -> np.ndarray:
    """(T,) bool: hours in which a truck may arrive and lift."""
    start, end = ocfg["truck_hours"]
    allowed = (tl.hour >= start) & (tl.hour < end)
    if ocfg["no_sunday"]:
        allowed &= tl.dow != 6
    if ocfg["no_holidays"]:
        allowed &= ~tl.holiday[tl.day_idx]
    return allowed


def next_allowed_index(allowed: np.ndarray) -> np.ndarray:
    """(T+1,) index of the first allowed hour >= t; T (= never) if none remains."""
    n = len(allowed)
    allowed_idx = np.flatnonzero(allowed)
    pos = np.searchsorted(allowed_idx, np.arange(n + 1))
    out = np.full(n + 1, n, dtype=np.int64)
    found = pos < len(allowed_idx)
    out[found] = allowed_idx[pos[found]]
    return out
