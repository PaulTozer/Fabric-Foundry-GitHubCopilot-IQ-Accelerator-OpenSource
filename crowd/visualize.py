"""Matplotlib visualisations for a `SimResult`.

Returns PNG bytes (or base64 strings). Uses the non-interactive 'Agg'
backend so we never need a display.
"""

from __future__ import annotations

import base64
import io

import matplotlib
matplotlib.use("Agg")  # must precede pyplot import; headless-safe.
import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.pyplot as plt        # noqa: E402
import numpy as np                     # noqa: E402
from shapely.geometry import Point     # noqa: E402

from .engine import SimResult


# Enterprise-ish palette (consistent with the WebUI brand).
_BRAND_RED = "#E60028"
_BRAND_GREY = "#3F3F3F"
_BRAND_LINE = "#D0D0D0"


def render_egress_curve(result: SimResult) -> bytes:
    """Plot people-remaining vs time. Returns raw PNG bytes."""
    fig, ax = plt.subplots(figsize=(7.2, 3.6), dpi=130)
    ts = [t for t, _ in result.egress_curve]
    ns = [n for _, n in result.egress_curve]
    ax.plot(ts, ns, color=_BRAND_RED, linewidth=2.2)
    ax.fill_between(ts, ns, color=_BRAND_RED, alpha=0.12)
    ax.set_xlabel("time (s)")
    ax.set_ylabel("agents remaining")
    title = (
        f"Egress curve — {result.scenario_name} "
        f"({result.agent_count} agents, seed={result.seed})"
    )
    ax.set_title(title, fontsize=10, color=_BRAND_GREY)
    ax.grid(True, linestyle=":", color=_BRAND_LINE)
    ax.set_xlim(0, max(ts) if ts else 1)
    ax.set_ylim(0, max(ns + [1]))

    # Annotate total egress time + completion status.
    status = "complete" if result.everyone_egressed else "TIMED OUT"
    ax.axvline(result.total_egress_time_sec, color=_BRAND_GREY,
                linestyle="--", linewidth=1)
    ax.text(
        result.total_egress_time_sec, max(ns + [1]) * 0.95,
        f" t={result.total_egress_time_sec:.1f}s · {status}",
        color=_BRAND_GREY, fontsize=9, va="top",
    )

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def render_density_heatmap(result: SimResult) -> bytes:
    """Render the time-averaged density grid with walkable mask + exits overlaid."""
    grid = result.density_grid.copy()
    xmin, ymin, xmax, ymax = result.grid_bounds

    # Mask cells outside the walkable polygon (set to NaN so colormap is "bad").
    ny, nx = grid.shape
    cell = (xmax - xmin) / nx
    mask = np.zeros_like(grid, dtype=bool)
    for j in range(ny):
        cy = ymin + (j + 0.5) * cell
        for i in range(nx):
            cx = xmin + (i + 0.5) * cell
            if not result.walkable_polygon.contains(Point(cx, cy)):
                mask[j, i] = True
    grid_m = np.ma.masked_array(grid, mask=mask)

    fig, ax = plt.subplots(figsize=(7.2, 5.4), dpi=130)
    cmap = plt.get_cmap("magma").copy()
    cmap.set_bad(color="#f5f5f5")
    vmax = max(float(grid_m.max()) if grid_m.count() else 0.0, 0.05)
    im = ax.imshow(
        grid_m,
        extent=(xmin, xmax, ymin, ymax),
        origin="lower",
        cmap=cmap,
        vmin=0,
        vmax=vmax,
        interpolation="nearest",
        aspect="equal",
    )

    # Walkable outline.
    xs, ys = result.walkable_polygon.exterior.xy
    ax.plot(xs, ys, color=_BRAND_GREY, linewidth=1.4)

    # Exits drawn as solid red rectangles.
    for label, polygon in result.exits:
        ex_xs = [p[0] for p in polygon] + [polygon[0][0]]
        ex_ys = [p[1] for p in polygon] + [polygon[0][1]]
        ax.fill(ex_xs, ex_ys, color=_BRAND_RED, alpha=0.85,
                 edgecolor=_BRAND_RED, linewidth=1.5, label=label)
        # Label centred above the exit.
        cx = sum(p[0] for p in polygon) / len(polygon)
        cy = sum(p[1] for p in polygon) / len(polygon)
        ax.annotate(label, xy=(cx, cy), xytext=(0, 8),
                     textcoords="offset points", ha="center",
                     fontsize=8, color=_BRAND_GREY)

    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.set_title(
        f"Time-averaged density — {result.scenario_name}  "
        f"(peak {result.peak_density_per_m2:.2f} p/m², "
        f"mean {result.mean_density_per_m2:.2f} p/m²)",
        fontsize=10, color=_BRAND_GREY,
    )

    cbar = fig.colorbar(im, ax=ax, shrink=0.78, label="people / m² (avg)")
    cbar.outline.set_visible(False)

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def to_base64(png_bytes: bytes) -> str:
    """Encode PNG bytes as a base64 data-URI payload (string only, no prefix)."""
    return base64.b64encode(png_bytes).decode("ascii")
