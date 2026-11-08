"""python -m softsensor  (run with the simulator venv: it needs pandas, pyarrow and LightGBM)."""
from __future__ import annotations

import logging
import sys

from .config import Settings, ensure_import_path


def build_api(settings: Settings):
    """Load the bundle + data and wire the API (also used by the tests)."""
    settings.validate()
    ensure_import_path(settings)
    from weight_train.bundle import SoftSensor

    from .api import SensorApi
    from .engine import LiveEngine, score_batch
    from .stores import load_all

    sensor = SoftSensor.load(settings.bundle_dir)
    data = load_all(settings, lambda tel, strokes: score_batch(sensor, tel, strokes))
    model_info = {"split": settings.split, "bundle": str(settings.bundle_dir.relative_to(settings.sim_root)),
                  "selection": sensor.selection, "conformal": sensor.conformal is not None,
                  "used_strokes": bool(sensor.info.get("used_strokes")), "n_models": len(sensor.models)}
    return SensorApi(data=data, engine=LiveEngine(sensor, data, settings.live_cache_size), model_info=model_info)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        stream=sys.stdout)
    settings = Settings.from_env()
    from .api import serve
    try:
        api = build_api(settings)
    except (FileNotFoundError, ValueError) as exc:
        logging.getLogger("softsensor").error("start-up failed: %s", exc)
        return 1
    serve(api, settings.host, settings.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
