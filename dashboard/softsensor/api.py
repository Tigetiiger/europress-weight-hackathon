"""JSON HTTP API (stdlib only). Bound to localhost; the Node dashboard server proxies /api/sensor/*.

Every response is an envelope: {"success": bool, "data": ... | null, "error": str | null}.

  GET /health
  GET /meta                              replay window, model, machines
  GET /fleet?at=<ISO hour>               every machine's state at that hour (batch job values)
  GET /machines/<BIN-nnnn>?at=&hours=    one machine: live model run at `at` + history up to `at`
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pandas as pd

from .engine import BusyError, LiveEngine
from .stores import TZ, SensorData

log = logging.getLogger(__name__)

BIN_RE = re.compile(r"^BIN-\d{4}$")
MACHINE_PATH = re.compile(r"^/machines/(BIN-\d{4})$")
MIN_HOURS, MAX_HOURS, DEFAULT_HOURS = 24, 24 * 120, 24 * 14
ORDER_FILL_PCT = 85.0          # SMART's default order level, for the "ready to empty" count
TRUCK_PAYLOAD_KG = 10_000.0    # approx. hook-lift truck payload limit (see simulator README)
NOTICE = ("Synthetic data from a physics-based simulator. Nothing here represents real Europress data "
          "or real Europress pressure settings.")


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _r(v, digits: int = 1):
    """JSON-safe rounded float (NaN/inf -> null)."""
    if v is None:
        return None
    v = float(v)
    return round(v, digits) if np.isfinite(v) else None


def _arr(a: np.ndarray, digits: int = 1) -> list:
    return [None if not np.isfinite(x) else round(float(x), digits) for x in a]


@dataclass
class SensorApi:
    data: SensorData
    engine: LiveEngine
    model_info: dict
    _fleet_cache: dict = field(default_factory=dict)
    _fleet_lock: threading.Lock = field(default_factory=threading.Lock)

    # ------------------------------------------------------------------ parsing
    def parse_at(self, query: dict) -> pd.Timestamp:
        raw = (query.get("at") or [""])[0]
        if not raw:
            return self.data.replay_start
        try:
            at = pd.Timestamp(raw)
            # ambiguous/non-existent local times (DST) raise here too -> 400, not 500
            at = (at.tz_localize(TZ) if at.tzinfo is None else at.tz_convert(TZ)).floor("h")
        except (ValueError, TypeError) as exc:
            raise ApiError(400, "at: not a valid ISO timestamp") from exc
        if not (self.data.replay_start <= at <= self.data.replay_end):
            raise ApiError(400, f"at: outside the replay window {self.data.replay_start.isoformat()} … "
                                f"{self.data.replay_end.isoformat()}")
        return at

    @staticmethod
    def parse_hours(query: dict) -> int:
        raw = (query.get("hours") or [str(DEFAULT_HOURS)])[0]
        if not raw.isdigit():
            raise ApiError(400, "hours: integer expected")
        return min(max(int(raw), MIN_HOURS), MAX_HOURS)

    # ------------------------------------------------------------------ handlers
    def health(self, _query: dict) -> dict:
        return {"ok": True, "machines": int(len(self.data.grid.bins))}

    def meta(self, _query: dict) -> dict:
        m = self.data.machines
        return {
            "notice": NOTICE,
            "replay": {"start": self.data.replay_start.isoformat(), "end": self.data.replay_end.isoformat(),
                       "hours": int(len(self.data.grid.hours)), "test_start": self.data.test_start.isoformat()},
            "model": self.model_info,
            "data": self.data.info,
            "thresholds": {"order_fill_pct": ORDER_FILL_PCT, "truck_payload_kg": TRUCK_PAYLOAD_KG},
            "machines": json.loads(m.to_json(orient="records")),
        }

    def fleet(self, query: dict) -> dict:
        at = self.parse_at(query)
        with self._fleet_lock:   # deterministic per hour: memoise (2190 hours max)
            cached = self._fleet_cache.get(at.value)
        if cached is None:
            cached = self._fleet_at(at)
            with self._fleet_lock:
                self._fleet_cache[at.value] = cached
        return cached

    def _fleet_at(self, at: pd.Timestamp) -> dict:
        g = self.data.grid
        j = g.hour_index(at)
        m = self.data.machines.set_index("bin_id").reindex(g.bins)
        pred, p10, p90 = g.pred[:, j], g.p10[:, j], g.p90[:, j]
        fill, hsl, truth = g.fill[:, j], g.hours_since_lift[:, j], g.truth[:, j]
        vol = m["container_volume_m3"].to_numpy(dtype=float)
        rows = [{"bin_id": b, "material": m.at[b, "material"], "region": m.at[b, "region"],
                 "equipment_class": m.at[b, "equipment_class"], "volume_m3": _r(vol[i]),
                 "fill_pct": _r(fill[i]), "hours_since_lift": _r(hsl[i], 0),
                 "mass_kg": _r(pred[i]), "p10_kg": _r(p10[i]), "p90_kg": _r(p90[i]),
                 "kg_m3": _r(pred[i] / vol[i]) if vol[i] else None, "truth_kg": _r(truth[i])}
                for i, b in enumerate(g.bins)]
        abs_err = np.abs(pred - truth)
        covered = (truth >= p10) & (truth <= p90)
        return {
            "at": at.isoformat(), "machines": rows,
            "totals": {
                "mass_t": _r(np.nansum(pred) / 1000.0, 1),
                "ready_to_empty": int(np.sum(fill >= ORDER_FILL_PCT)),
                "heavy": int(np.sum(p90 >= TRUCK_PAYLOAD_KG)),
                "mean_fill_pct": _r(np.nanmean(fill)),
            },
            # simulator truth: only possible in simulation, shown as evaluation
            "evaluation": {"mae_kg": _r(np.nanmean(abs_err)), "truth_t": _r(np.nansum(truth) / 1000.0, 1),
                           "coverage": _r(float(np.mean(covered)), 3)},
        }

    def machine(self, bin_id: str, query: dict) -> dict:
        g = self.data.grid
        i = g.bin_index(bin_id)
        if i is None:
            raise ApiError(404, f"unknown machine {bin_id}")
        at = self.parse_at(query)
        hours = self.parse_hours(query)
        j = g.hour_index(at)
        j0 = max(0, j - hours + 1)
        span = slice(j0, j + 1)
        try:
            live = self.engine.estimate(bin_id, at)
        except LookupError as exc:
            raise ApiError(404, str(exc)) from exc
        except BusyError as exc:
            raise ApiError(503, "model busy — try again shortly") from exc
        info = self.data.machines.set_index("bin_id").loc[bin_id]
        return {
            "at": at.isoformat(),
            "machine": json.loads(info.to_json()) | {"bin_id": bin_id},
            "live": live,
            "batch": {"mass_kg": _r(g.pred[i, j]), "p10_kg": _r(g.p10[i, j]), "p90_kg": _r(g.p90[i, j])},
            "now": {"fill_pct": _r(g.fill[i, j]), "hours_since_lift": _r(g.hours_since_lift[i, j], 0)},
            "truth_kg": _r(g.truth[i, j]),
            "series": {
                "t": [int(x) for x in (g.hours[span].as_unit("ns").asi8 // 1_000_000)],
                "mass_kg": _arr(g.pred[i, span]), "p10_kg": _arr(g.p10[i, span]), "p90_kg": _arr(g.p90[i, span]),
                "fill_pct": _arr(g.fill[i, span]), "truth_kg": _arr(g.truth[i, span]),
            },
            "lifts": self._lifts(bin_id, i, g.hours[j0], at),
        }

    def _lifts(self, bin_id: str, i: int, start: pd.Timestamp, at: pd.Timestamp) -> list:
        """Emptyings up to `at`, each with the estimate one hour before vs the weighbridge ticket."""
        lifts = self.data.lifts
        sel = lifts[(lifts["bin_id"] == bin_id) & (lifts["ts"] >= max(start, self.data.replay_start + pd.Timedelta(hours=1)))
                    & (lifts["ts"] <= at)]
        g = self.data.grid
        out = []
        for row in sel.itertuples(index=False):
            j = g.hour_index(row.ts - pd.Timedelta(hours=1))
            if j is None:
                continue
            out.append({"ts": row.ts.isoformat(), "t": int(row.ts.as_unit("ns").value // 1_000_000), "trigger": row.trigger,
                        "pred_kg": _r(g.pred[i, j]), "p10_kg": _r(g.p10[i, j]), "p90_kg": _r(g.p90[i, j]),
                        "weighbridge_kg": _r(row.weighbridge_net_kg),
                        "truth_kg": _r(getattr(row, "true_mass_removed_kg", np.nan))})
        return out

    # ------------------------------------------------------------------ dispatch
    def dispatch(self, path: str, query: dict) -> dict:
        if path == "/health":
            return self.health(query)
        if path == "/meta":
            return self.meta(query)
        if path == "/fleet":
            return self.fleet(query)
        match = MACHINE_PATH.match(path)
        if match:
            return self.machine(match.group(1), query)
        raise ApiError(404, "not found")


def make_handler(api: SensorApi):
    class Handler(BaseHTTPRequestHandler):
        server_version = "softsensor"
        sys_version = ""

        def do_GET(self) -> None:  # noqa: N802 (stdlib name)
            t0 = time.perf_counter()
            parts = urlsplit(self.path)
            status, body = 200, None
            try:
                body = {"success": True, "data": api.dispatch(parts.path, parse_qs(parts.query)), "error": None}
            except ApiError as exc:
                status, body = exc.status, {"success": False, "data": None, "error": exc.message}
            except Exception:  # never leak internals to the client; log them here
                log.exception("request failed: %s", parts.path)
                status, body = 500, {"success": False, "data": None, "error": "internal error"}
            payload = json.dumps(body, separators=(",", ":"), allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            log.info(json.dumps({"path": parts.path, "status": status, "ms": round((time.perf_counter() - t0) * 1000, 1)}))

        def log_message(self, *_args) -> None:  # replaced by the structured line above
            return

    return Handler


def serve(api: SensorApi, host: str, port: int) -> None:
    httpd = ThreadingHTTPServer((host, port), make_handler(api))
    httpd.daemon_threads = True
    log.info("soft sensor API listening on http://%s:%d", host, port)
    httpd.serve_forever()
