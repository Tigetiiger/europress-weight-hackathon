"""Collection policy: on-call orders from the observed fill plus a max interval."""
from __future__ import annotations

from typing import Any, Mapping

import numpy as np

TRIGGER_FILL = 1
TRIGGER_MAX_INTERVAL = 2
TRIGGER_NAMES = {TRIGGER_FILL: "fill", TRIGGER_MAX_INTERVAL: "max_interval"}


class Dispatcher:
    """Order state per bin. A truck arrives 4-30 h after the order, in service hours."""

    def __init__(self, threshold_pct: np.ndarray, next_allowed: np.ndarray,
                 ocfg: Mapping[str, Any], rng: np.random.Generator):
        n = len(threshold_pct)
        self.threshold = threshold_pct
        self.next_allowed = next_allowed
        self.delay_h = tuple(ocfg["truck_delay_h"])
        self.max_age_h = ocfg["max_interval_days"] * 24 - ocfg["scheduled_lead_h"]
        self.rng = rng
        self.pending = np.zeros(n, dtype=bool)
        self.arrival = np.full(n, -1, dtype=np.int64)
        self.order_hour = np.full(n, -1, dtype=np.int64)
        self.trigger = np.zeros(n, dtype=np.int8)
        self.false_full = np.zeros(n, dtype=bool)
        self.obs_fill = np.full(n, np.nan)
        self.true_fill = np.full(n, np.nan)

    def due(self, h: int) -> np.ndarray:
        return np.flatnonzero(self.pending & (self.arrival == h))

    def order(self, h: int, reading: np.ndarray, age_h: np.ndarray,
              ff_active: np.ndarray, true_fill: np.ndarray, unbridged: np.ndarray | None = None) -> None:
        """Place orders at the end of hour ``h`` from the (noisy) reading."""
        with np.errstate(invalid="ignore"):
            by_fill = ~self.pending & (reading >= self.threshold)
        by_age = ~self.pending & ~by_fill & (age_h >= self.max_age_h)
        idx = np.flatnonzero(by_fill | by_age)
        if idx.size == 0:
            return
        delay = np.ceil(self.rng.uniform(*self.delay_h, idx.size)).astype(np.int64)
        target = np.minimum(h + 1 + delay, len(self.next_allowed) - 1)
        self.arrival[idx] = self.next_allowed[target]
        self.pending[idx] = True
        self.order_hour[idx] = h
        self.trigger[idx] = np.where(by_fill[idx], TRIGGER_FILL, TRIGGER_MAX_INTERVAL)
        # a fill order the sensor would not have placed without material bridging
        clean = unbridged[idx] if unbridged is not None else np.zeros(idx.size)
        self.false_full[idx] = (by_fill[idx] & ff_active[idx] & (true_fill[idx] < self.threshold[idx])
                                & (clean < self.threshold[idx]))
        self.obs_fill[idx] = reading[idx]
        self.true_fill[idx] = true_fill[idx]

    def complete(self, idx: np.ndarray) -> None:
        self.pending[idx] = False
        self.arrival[idx] = -1
