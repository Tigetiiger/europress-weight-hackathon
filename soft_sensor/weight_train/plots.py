"""PNG figures for a trained folder: python -m weight_train report --models <dir>.

Reads metrics.json, <split>/test_predictions.parquet and <split>/shap_<material>.parquet
and writes <models_dir>/plots/*.png (matplotlib only, Agg backend).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

log = logging.getLogger(__name__)

SPLIT_ORDER = ("time", "bins")
SPLIT_LABEL = {"time": "same machines, later months", "bins": "unseen machines"}
SPLIT_HATCH = {"time": "", "bins": "///"}
TARGET_PCT = 5.0
TARGET_COVERAGE = 0.8
TOP_IMPORTANCE, TOP_SHAP = 15, 12
DENSE_SCATTER = 600
CURVE_DAYS, CURVE_OFFSET_DAYS, CURVE_MACHINES = 21, 7, 3
INK, MUTED, GRID, OVERALL = "#1f1f1e", "#6b6a66", "#e4e3df", "#52514e"
MATERIAL_COLOURS = {"cardboard": "#eb6834", "paper": "#2a78d6", "plastic_film": "#1baf7a",
                    "mixed_waste": "#6250d6"}
FALLBACK_COLOURS = ["#eda100", "#e87ba4", "#008300", "#e34948"]
MARKERS = ["o", "s", "^", "D", "v", "P", "X", "*"]
VALUE_CMAP = LinearSegmentedColormap.from_list("value", ["#a9cbf0", "#2a78d6", "#0b2f66"])
LADDER_STEPS = {"naive_fill_x_density": "naive\nfill x density", "fill_presses": "fill +\npresses",
                "pressure": "+ pressure\n(peak)", "pressure_graph": "+ pressure\ngraph",
                "full_shared": "+ weather\n(full, shared)",
                "full_per_material": "full,\nper material", "full_selected": "selected"}


# ---------------------------------------------------------------- helpers
def load_metrics(models_dir: Path) -> dict:
    path = Path(models_dir) / "metrics.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found - train first")
    return json.loads(path.read_text())


def splits_of(metrics: dict) -> list[str]:
    present = metrics.get("splits", {})
    return [s for s in SPLIT_ORDER if s in present] + [s for s in present if s not in SPLIT_ORDER]


def materials_of(metrics: dict) -> list[str]:
    mats: set[str] = set()
    for sp in metrics.get("splits", {}).values():
        mats.update(sp.get("final", {}).get("lift", {}).get("by_material", {}))
    known = [m for m in MATERIAL_COLOURS if m in mats]
    return known + sorted(mats - set(known))


def material_colour(mat: str, materials: list[str]) -> str:
    if mat in MATERIAL_COLOURS:
        return MATERIAL_COLOURS[mat]
    extra = [m for m in materials if m not in MATERIAL_COLOURS]
    return FALLBACK_COLOURS[extra.index(mat) % len(FALLBACK_COLOURS)] if mat in extra else MUTED


def metric(d: dict | None, key: str) -> float:
    value = (d or {}).get(key)
    return float(value) if isinstance(value, (int, float)) and value is not None else float("nan")


def _style() -> None:
    plt.rcParams.update({
        "figure.dpi": 110, "savefig.dpi": 130, "font.size": 10, "axes.titlesize": 11,
        "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED,
        "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
        "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True, "legend.frameon": False,
        "figure.facecolor": "white", "axes.facecolor": "white", "text.color": INK,
    })


def _save(fig: plt.Figure, path: Path) -> Path:
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def _read_predictions(models_dir: Path, split: str) -> pd.DataFrame | None:
    path = Path(models_dir) / split / "test_predictions.parquet"
    return pd.read_parquet(path) if path.exists() else None


def _target_line(ax: plt.Axes, value: float, label: str) -> plt.Line2D:
    return ax.axhline(value, color=INK, lw=1, ls="--", zorder=1, label=label)


# ---------------------------------------------------------------- (a) predicted vs true
def plot_pred_vs_true(models_dir: Path, split: str, materials: list[str], out: Path) -> Path | None:
    df = _read_predictions(models_dir, split)
    if df is None or not df["is_lift"].any():
        return None
    lift = df[df["is_lift"]]
    fig, ax = plt.subplots(figsize=(6.4, 6))
    hi = float(np.nanmax(lift[["y_kg", "pred_kg"]].to_numpy())) * 1.05
    grid = np.array([0.0, hi])
    size, alpha, edge = (28, 0.85, 0.6) if len(lift) < DENSE_SCATTER else (9, 0.6, 0.0)
    ax.fill_between(grid, grid * 0.95, grid * 1.05, color=GRID, alpha=0.9, lw=0, label="±5 % band")
    ax.plot(grid, grid, color=INK, lw=1, label="1:1")
    for i, mat in enumerate(m for m in materials if m in set(lift["material"])):
        g = lift[lift["material"] == mat]
        ax.scatter(g["y_kg"], g["pred_kg"], s=size, marker=MARKERS[i % len(MARKERS)], alpha=alpha,
                   color=material_colour(mat, materials), edgecolor="white", linewidth=edge,
                   label=f"{mat} (n={len(g)})")
    ax.set(xlim=(0, hi), ylim=(0, hi), xlabel="weighbridge mass at lift (kg)",
           ylabel="predicted mass, hour before lift (kg)", aspect="equal")
    ax.set_title(f"Predicted vs true at lift - {split} split ({SPLIT_LABEL.get(split, split)})")
    ax.legend(loc="upper left", fontsize=8)
    return _save(fig, out / f"pred_vs_true_{split}.png")


# ---------------------------------------------------------------- (b)/(c) grouped bars by material
def _grouped_bars(ax: plt.Axes, metrics: dict, materials: list[str], getter: Callable[[dict, str], float],
                  scale: float, fmt: str) -> list:
    splits = splits_of(metrics)
    groups = materials + ["overall"]
    width = 0.8 / max(len(splits), 1)
    for j, split in enumerate(splits):
        sp = metrics["splits"][split]
        vals = np.array([getter(sp, g) for g in groups]) * scale
        xs = np.arange(len(groups)) + (j - (len(splits) - 1) / 2) * width
        colours = [material_colour(g, materials) if g != "overall" else OVERALL for g in groups]
        bars = ax.bar(xs, np.nan_to_num(vals), width * 0.92, color=colours, hatch=SPLIT_HATCH.get(split, ".."),
                      edgecolor="white", alpha=1.0 if j == 0 else 0.55, linewidth=0.8)
        for b, v in zip(bars, vals):
            label = "n/a" if np.isnan(v) else fmt.format(v)
            ax.annotate(label, (b.get_x() + b.get_width() / 2, 0 if np.isnan(v) else v), ha="center",
                        va="bottom", fontsize=7, color=INK, xytext=(0, 2), textcoords="offset points")
    ax.set_xticks(np.arange(len(groups)), [g.replace("_", "\n") for g in groups])
    handles = [Patch(facecolor=OVERALL, alpha=1.0 if j == 0 else 0.55, hatch=SPLIT_HATCH.get(s, ".."),
                     edgecolor="white", label=f"{s}: {SPLIT_LABEL.get(s, s)}") for j, s in enumerate(splits)]
    return handles


def _by_material(sp: dict, scope: str, group: str, key: str) -> float:
    block = sp.get("final", {}).get(scope, {})
    return metric(block.get("overall") if group == "overall" else block.get("by_material", {}).get(group), key)


def plot_lift_error(metrics: dict, materials: list[str], out: Path) -> Path:
    fig, ax = plt.subplots(figsize=(8, 4.6))
    handles = _grouped_bars(ax, metrics, materials, lambda sp, g: _by_material(sp, "lift", g, "mean_pct"),
                            1.0, "{:.1f}")
    ax.legend(handles=handles + [_target_line(ax, TARGET_PCT, "5 % target")], loc="upper left", fontsize=8)
    ax.set(ylabel="mean |error| at lift (%)", title="Mean % error at lift by material (selected model)")
    ax.set_ylim(0, max(ax.get_ylim()[1], TARGET_PCT * 1.4))
    return _save(fig, out / "lift_error_by_material.png")


def plot_coverage(metrics: dict, materials: list[str], out: Path) -> Path:
    target = metrics.get("settings", {}).get("target_coverage", TARGET_COVERAGE)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6), sharey=True)
    for ax, scope in zip(axes, ("lift", "hourly")):
        handles = _grouped_bars(ax, metrics, materials,
                                lambda sp, g, s=scope: _by_material(sp, s, g, "coverage"), 100.0, "{:.0f}")
        handles.append(_target_line(ax, target * 100, f"{target:.0%} target"))
        ax.set(title=f"p10-p90 interval coverage - {scope}", ylim=(0, 109))
    axes[0].set_ylabel("share of true masses inside interval (%)")
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, -0.04), ncol=3, fontsize=9)
    return _save(fig, out / "coverage_by_material.png")


# ---------------------------------------------------------------- (d) feature importance
def _importance_groups(sp: dict) -> list[tuple[str, dict]]:
    """Materials served by the same model have identical gains: plot each model once."""
    groups: list[tuple[list[str], dict]] = []
    for mat, gains in sp.get("feature_importance_gain", {}).items():
        match = next((g for g in groups if g[1] == gains), None)
        if match:
            match[0].append(mat)
        else:
            groups.append(([mat], gains))
    selection = sp.get("selection", {})
    out = []
    for mats, gains in groups:
        kind = "shared model" if selection.get(mats[0]) == "shared" or len(mats) > 1 else "per-material model"
        out.append((f"{kind}: {', '.join(mats)}", gains))
    return out


def plot_importance(metrics: dict, split: str, materials: list[str], out: Path) -> Path | None:
    groups = _importance_groups(metrics["splits"][split])
    if not groups:
        return None
    fig, axes = plt.subplots(1, len(groups), figsize=(5.2 * len(groups), 5.2), squeeze=False)
    for ax, (title, gains) in zip(axes[0], groups):
        total = sum(gains.values()) or 1.0
        top = sorted(gains.items(), key=lambda kv: -kv[1])[:TOP_IMPORTANCE][::-1]
        first = title.split(": ")[1].split(", ")[0]
        colour = OVERALL if title.startswith("shared") else material_colour(first, materials)
        ax.barh([k for k, _ in top], [v / total * 100 for _, v in top], color=colour, height=0.7)
        ax.set(xlabel="share of total gain (%)", title=title)
        ax.grid(axis="y", visible=False)
        ax.tick_params(axis="y", labelsize=8)
    fig.suptitle(f"Feature importance (gain, top {TOP_IMPORTANCE}) - {split} split, selected models", y=1.01)
    fig.tight_layout()
    return _save(fig, out / f"feature_importance_{split}.png")


# ---------------------------------------------------------------- (e) SHAP beeswarm
def _value_rank(values: pd.Series) -> np.ndarray | None:
    if not (pd.api.types.is_numeric_dtype(values) or pd.api.types.is_bool_dtype(values)):
        return None
    return values.astype(float).rank(pct=True).to_numpy()


def _beeswarm(ax: plt.Axes, shap: pd.DataFrame, rng: np.random.Generator) -> None:
    feats = [c[len("shap__"):] for c in shap.columns if c.startswith("shap__")]
    order = sorted(feats, key=lambda f: -shap[f"shap__{f}"].abs().mean())[:TOP_SHAP][::-1]
    for row, feat in enumerate(order):
        x = shap[f"shap__{feat}"].to_numpy(float)
        y = row + rng.uniform(-0.3, 0.3, len(x)) * min(1.0, 0.3 + len(x) / 200)
        vcol = f"value__{feat}"
        rank = _value_rank(shap[vcol]) if vcol in shap else None
        if rank is None:
            ax.scatter(x, y, s=9, color=MUTED, alpha=0.7, lw=0)
        else:
            ax.scatter(x, y, s=9, c=rank, cmap=VALUE_CMAP, vmin=0, vmax=1, alpha=0.85, lw=0)
    ax.axvline(0, color=MUTED, lw=0.8)
    ax.set_yticks(range(len(order)), order, fontsize=8)
    ax.grid(axis="y", visible=False)


def plot_shap(models_dir: Path, split: str, materials: list[str], out: Path) -> Path | None:
    files = {m: Path(models_dir) / split / f"shap_{m}.parquet" for m in materials}
    files = {m: p for m, p in files.items() if p.exists()}
    if not files:
        return None
    ncol = 2 if len(files) > 1 else 1
    nrow = int(np.ceil(len(files) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(6.4 * ncol, 4.6 * nrow), squeeze=False,
                             layout="constrained")
    rng = np.random.default_rng(0)
    for ax, (mat, path) in zip(axes.flat, files.items()):
        shap = pd.read_parquet(path)
        _beeswarm(ax, shap, rng)
        ax.set_title(f"{mat} (n={len(shap)} test lifts)", color=material_colour(mat, materials))
        ax.set_xlabel("SHAP contribution to density (kg/m³)")
    for ax in list(axes.flat)[len(files):]:
        ax.set_visible(False)
    sm = plt.cm.ScalarMappable(cmap=VALUE_CMAP, norm=plt.Normalize(0, 1))
    cbar = fig.colorbar(sm, ax=axes.ravel().tolist(), shrink=0.6, pad=0.01)
    cbar.set_ticks([0, 1], labels=["low", "high"])
    cbar.set_label("feature value (rank within feature; grey = categorical)")
    fig.suptitle(f"SHAP summary at lift - {split} split (top {TOP_SHAP} features by mean |SHAP|)")
    return _save(fig, out / f"shap_{split}.png")


# ---------------------------------------------------------------- (f) hourly mass curves
def _pick_machines(df: pd.DataFrame, materials: list[str]) -> list[str]:
    lifts = df[df["is_lift"]].groupby(["material", "bin_id"]).size().reset_index(name="n")
    lifts = lifts.sort_values("n", ascending=False)
    picked = [g.iloc[0]["bin_id"] for _, g in lifts.groupby("material", sort=False)][:CURVE_MACHINES]
    rest = [b for b in lifts["bin_id"] if b not in picked]
    picked += rest[: CURVE_MACHINES - len(picked)]
    return picked or list(df["bin_id"].unique()[:CURVE_MACHINES])


def plot_mass_curves(models_dir: Path, split: str, materials: list[str], out: Path) -> Path | None:
    df = _read_predictions(models_dir, split)
    if df is None or df.empty:
        return None
    t0 = df["ts"].min() + pd.Timedelta(days=CURVE_OFFSET_DAYS)
    if t0 + pd.Timedelta(days=CURVE_DAYS) > df["ts"].max():
        t0 = df["ts"].min()
    window = df[(df["ts"] >= t0) & (df["ts"] < t0 + pd.Timedelta(days=CURVE_DAYS))]
    machines = _pick_machines(window, materials)
    fig, axes = plt.subplots(len(machines), 1, figsize=(12, 2.9 * len(machines) + 0.6), sharex=True,
                             squeeze=False)
    for ax, bid in zip(axes[:, 0], machines):
        g = window[window["bin_id"] == bid].sort_values("ts")
        mat = str(g["material"].iloc[0])
        colour = material_colour(mat, materials)
        ax.fill_between(g["ts"], g["lo_kg"], g["hi_kg"], color=colour, alpha=0.2, lw=0, label="p10-p90")
        ax.plot(g["ts"], g["y_kg"], color=INK, lw=1.2, label="true mass")
        ax.plot(g["ts"], g["pred_kg"], color=colour, lw=1.6, label="predicted")
        lift = g[g["is_lift"]]
        ax.scatter(lift["ts"], lift["y_kg"], marker="v", s=46, color=INK, zorder=5, label="hour before lift")
        ax.set_title(f"{bid} - {mat}, {g['container_volume_m3'].iloc[0]:.0f} m³, {g['infeed'].iloc[0]}",
                     loc="left", fontsize=10)
        ax.set_ylabel("mass (kg)")
    fig.suptitle(f"Hourly mass estimate, {CURVE_DAYS} days of the test period - {split} split")
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=4, fontsize=9, bbox_to_anchor=(0.5, 0.965))
    return _save(fig, out / f"mass_curves_{split}.png")


# ---------------------------------------------------------------- (g) learning curve, (h) ladder
def plot_learning_curve(metrics: dict, materials: list[str], out: Path) -> Path | None:
    curve = metrics.get("splits", {}).get("bins", {}).get("learning_curve") or []
    if len(curve) < 1:
        return None
    ns = [c["n_machines"] for c in curve]
    fig, ax = plt.subplots(figsize=(8.5, 4.6))
    for i, mat in enumerate(materials):
        ys = [metric(c.get("by_material", {}).get(mat), "mean_pct") for c in curve]
        ax.plot(ns, ys, marker=MARKERS[i % len(MARKERS)], color=material_colour(mat, materials), lw=1.5,
                label=mat)
    ax.plot(ns, [metric(c.get("overall"), "mean_pct") for c in curve], marker="o", color=INK, lw=2.6,
            label="overall")
    _target_line(ax, TARGET_PCT, "5 % target")
    ax.set(xlabel="training machines", ylabel="mean |error| at lift (%), unseen machines",
           title="Learning curve - shared model, bins split", ylim=(0, None))
    if ns[-1] > 4 * ns[0]:
        ax.set_xscale("log")
    ax.set_xticks(ns, [str(n) for n in ns])
    ax.minorticks_off()
    ax.legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.01, 1))
    return _save(fig, out / "learning_curve.png")


def plot_ladder(metrics: dict, materials: list[str], out: Path) -> Path | None:
    splits = [s for s in splits_of(metrics) if metrics["splits"][s].get("ladder")]
    if not splits:
        return None
    fig, axes = plt.subplots(1, len(splits), figsize=(6.5 * len(splits), 4.8), sharey=True, squeeze=False)
    for ax, split in zip(axes[0], splits):
        ladder = metrics["splits"][split]["ladder"]
        steps = [s for s in LADDER_STEPS if s in ladder]
        xs = np.arange(len(steps))
        for i, mat in enumerate(materials):
            ys = [metric(ladder[s].get("by_material", {}).get(mat), "mean_pct") for s in steps]
            ax.plot(xs, ys, marker=MARKERS[i % len(MARKERS)], color=material_colour(mat, materials), lw=1.4,
                    label=mat)
        overall = [metric(ladder[s].get("overall"), "mean_pct") for s in steps]
        ax.plot(xs, overall, marker="o", color=INK, lw=2.6, label="overall")
        for x, y in zip(xs, overall):
            if not np.isnan(y):
                ax.annotate(f"{y:.1f}", (x, y), xytext=(14, 8), textcoords="offset points", ha="center",
                            fontsize=8, fontweight="bold", bbox={"boxstyle": "round,pad=0.15", "fc": "white",
                                                                 "ec": "none", "alpha": 0.85})
        _target_line(ax, TARGET_PCT, "5 % target")
        ax.set_yscale("log")
        ax.set_yticks([1, 2, 5, 10, 20, 50, 100], ["1", "2", "5", "10", "20", "50", "100"])
        ax.set_xticks(xs, [LADDER_STEPS[s] for s in steps], fontsize=8)
        ax.set_title(f"{split} split ({SPLIT_LABEL.get(split, split)})")
    axes[0, 0].set_ylabel("mean |error| at lift (%), log scale")
    axes[0, -1].legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.01, 1))
    fig.suptitle("Comparison ladder: what each signal adds", y=1.01)
    return _save(fig, out / "ladder.png")


# ---------------------------------------------------------------- entry point
def _run(name: str, fn: Callable[[], Path | None]) -> Path | None:
    try:
        return fn()
    except Exception:  # one broken figure must not block the rest of the report
        log.exception("plot %s failed", name)
        return None


def make_all_plots(models_dir: Path) -> list[Path]:
    models_dir = Path(models_dir)
    metrics = load_metrics(models_dir)
    out = models_dir / "plots"
    out.mkdir(parents=True, exist_ok=True)
    _style()
    mats = materials_of(metrics)
    splits = splits_of(metrics)
    jobs: list[tuple[str, Callable[[], Path | None]]] = []
    for s in splits:
        jobs += [(f"pred_vs_true_{s}", lambda s=s: plot_pred_vs_true(models_dir, s, mats, out)),
                 (f"feature_importance_{s}", lambda s=s: plot_importance(metrics, s, mats, out)),
                 (f"shap_{s}", lambda s=s: plot_shap(models_dir, s, mats, out))]
    jobs += [("lift_error_by_material", lambda: plot_lift_error(metrics, mats, out)),
             ("coverage_by_material", lambda: plot_coverage(metrics, mats, out)),
             ("learning_curve", lambda: plot_learning_curve(metrics, mats, out)),
             ("ladder", lambda: plot_ladder(metrics, mats, out))]
    if splits:
        curve_split = "time" if "time" in splits else splits[0]
        jobs.append(("mass_curves", lambda: plot_mass_curves(models_dir, curve_split, mats, out)))
    paths = [p for p in (_run(name, fn) for name, fn in jobs) if p is not None]
    log.info("wrote %d plots to %s", len(paths), out)
    return paths
