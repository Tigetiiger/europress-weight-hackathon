"""Service settings, read once from environment variables."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

# final_codebase/soft_sensor, next to this dashboard folder
DEFAULT_SIM_ROOT = str(Path(__file__).resolve().parents[2] / "soft_sensor")


@dataclass(frozen=True)
class Settings:
    sim_root: Path
    run: str = "demo40"             # runs/<run>: simulated telemetry (demo40 = shipped 40-machine slice)
    models: str = "full400"         # models/<models>: trained bundles + batch predictions
    split: str = "time"             # bundle used for live inference ("time" = pilot fleet)
    host: str = "127.0.0.1"
    port: int = 8093
    lookback_days: int = 40         # telemetry loaded before the replay window (max cycle 28 d + 8 d weather)
    live_cache_size: int = 2048

    @property
    def run_dir(self) -> Path:
        return self.sim_root / "runs" / self.run

    @property
    def model_dir(self) -> Path:
        return self.sim_root / "models" / self.models

    @property
    def bundle_dir(self) -> Path:
        return self.model_dir / self.split

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        return cls(
            sim_root=Path(env.get("SOFTSENSOR_SIM_ROOT", DEFAULT_SIM_ROOT)),
            run=env.get("SOFTSENSOR_RUN", "demo40"),
            models=env.get("SOFTSENSOR_MODELS", "full400"),
            split=env.get("SOFTSENSOR_SPLIT", "time"),
            host=env.get("SOFTSENSOR_HOST", "127.0.0.1"),
            port=int(env.get("SOFTSENSOR_PORT", "8093")),
        )

    def validate(self) -> None:
        for path in (self.run_dir / "hourly.parquet", self.run_dir / "bins.parquet",
                     self.run_dir / "lifts.parquet", self.bundle_dir / "bundle.json"):
            if not path.is_file():
                raise FileNotFoundError(f"soft sensor: required file missing: {path}")
        if self.split not in ("time", "bins"):
            raise ValueError(f"soft sensor: split must be 'time' or 'bins', got {self.split!r}")


def ensure_import_path(settings: Settings) -> None:
    """Make the simulator's ``weight_train`` package importable (single source of the model code)."""
    root = str(settings.sim_root)
    if root not in sys.path:
        sys.path.insert(0, root)
