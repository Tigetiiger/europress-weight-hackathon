"""The deployable soft sensor: models + selection + conformal + pressure inverter, saved as text/JSON."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from .conformal import Conformal
from .features import Features
from .modeling import FittedModel
from .physics import PressureInverter

KINDS = ("point", "q_lo", "q_hi")


def model_key(setup: str, group: str, kind: str) -> str:
    return f"{setup}__{group}__{kind}"


@dataclass
class SoftSensor:
    inverter: PressureInverter
    models: dict = field(default_factory=dict)       # model_key -> FittedModel
    selection: dict = field(default_factory=dict)    # material -> "shared" | "per_material"
    conformal: Conformal | None = None
    info: dict = field(default_factory=dict)

    def _group(self, material: str) -> tuple[str, str]:
        setup = self.selection.get(material, "shared")
        return setup, ("all" if setup == "shared" else material)

    def predict_kg_m3(self, X: pd.DataFrame, materials: np.ndarray, setup: str | None = None):
        """(point, lo, hi) in kg/m3; uses the selected setup per material unless ``setup`` is given."""
        n = len(X)
        point, lo, hi = np.zeros(n), np.zeros(n), np.zeros(n)
        for mat in np.unique(materials):
            sel = np.flatnonzero(materials == mat)
            chosen, group = (setup, "all" if setup == "shared" else mat) if setup else self._group(mat)
            Xs = X.iloc[sel]
            point[sel] = self.models[model_key(chosen, group, "point")].predict(Xs)
            lo_key, hi_key = model_key(chosen, group, "q_lo"), model_key(chosen, group, "q_hi")
            if lo_key in self.models:
                a, b = self.models[lo_key].predict(Xs), self.models[hi_key].predict(Xs)
                lo[sel], hi[sel] = np.minimum(a, b), np.maximum(a, b)
            else:
                lo[sel], hi[sel] = point[sel], point[sel]
        if self.conformal is not None:
            lo, hi = self.conformal.apply(lo, hi, point, materials, X["fill_ffill"].to_numpy())
        point = np.maximum(point, 0.0)
        lo = np.clip(np.minimum(lo, point), 0.0, None)
        hi = np.maximum(hi, point)
        return point, lo, hi

    def predict_frame(self, feats: Features) -> pd.DataFrame:
        meta = feats.meta
        point, lo, hi = self.predict_kg_m3(feats.X, meta["material"].to_numpy().astype(str))
        vol = meta["container_volume_m3"].to_numpy()
        return pd.DataFrame({"bin_id": meta["bin_id"].to_numpy(), "ts": meta["ts"].reset_index(drop=True),
                             "material": meta["material"].to_numpy(), "mass_pred_kg": point * vol,
                             "mass_p10_kg": lo * vol, "mass_p90_kg": hi * vol})

    # ------------------------------------------------------------------ persistence
    def save(self, out: Path) -> None:
        out.mkdir(parents=True, exist_ok=True)
        manifest = {}
        for key, m in self.models.items():
            m.booster.save_model(str(out / f"{key}.txt"), num_iteration=m.best_iter)
            manifest[key] = {"features": m.features, "best_iter": m.best_iter, "params": m.params,
                             "monotone": m.monotone, "objective": m.objective}
        bundle = {"models": manifest, "selection": self.selection, "inverter": self.inverter.to_dict(),
                  "conformal": self.conformal.to_dict() if self.conformal else None, "info": self.info}
        (out / "bundle.json").write_text(json.dumps(bundle, indent=2, default=str))

    @classmethod
    def load(cls, path: Path) -> "SoftSensor":
        bundle = json.loads((path / "bundle.json").read_text())
        models = {}
        for key, m in bundle["models"].items():
            booster = lgb.Booster(model_file=str(path / f"{key}.txt"))
            models[key] = FittedModel(booster, m["features"], booster.current_iteration(), m["params"],
                                      m["monotone"], m["objective"])
        conf = Conformal.from_dict(bundle["conformal"]) if bundle["conformal"] else None
        return cls(PressureInverter.from_dict(bundle["inverter"]), models, bundle["selection"], conf, bundle["info"])
