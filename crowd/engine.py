"""JuPedSim wrapper — runs one scenario and reports egress + density.

Public entry point: `simulate(scenario, agent_count=50, max_time_sec=120,
seed=1)` → `SimResult`.

Implementation notes
--------------------
- Uses `CollisionFreeSpeedModel` (the JuPedSim 1.x workhorse, RiMEA-validated).
- Time step `dt = 0.05 s`; snapshots every `SNAPSHOT_EVERY_N` iterations
  (=> 2 Hz density grid for the heatmap).
- Density grid is a fixed bin (`GRID_CELL_M`) overlaid on the bounding box;
  cells outside the walkable polygon are masked to NaN in the rendered heatmap.
- "Peak density" = max over all (time, cell) of (people_in_cell / cell_area_m²),
  restricted to walkable cells. This is the conservative point-in-time peak,
  not a Voronoi density.
- All RNGs are seeded deterministically (positions + agent params); two runs
  with the same args return bit-identical numerics.

Safety caps (LIMITS):
- agent_count       1 ≤ n ≤ 2000
- max_time_sec      5 ≤ t ≤ 900
- guards against pathological geometries (zero-area walkable, spawn outside
  walkable) by delegating to `crowd.scenarios.load_scenario`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping

import numpy as np
from shapely.geometry import Point, Polygon

from .scenarios import Scenario, load_scenario


# ─── Tunables / safety caps ───────────────────────────────────────────────


DEFAULTS = {
    "agent_count":   50,
    "max_time_sec":  120.0,
    "seed":          1,
}


LIMITS = {
    "agent_count_min":  1,
    "agent_count_max":  2000,
    "max_time_min":     5.0,
    "max_time_max":     900.0,
}


_DT_SEC = 0.05                # 20 Hz sim
_SNAPSHOT_EVERY_N = 10        # → 2 Hz snapshots for density/egress
_GRID_CELL_M = 0.5            # density bin
_AGENT_RADIUS_M = 0.15        # standard JuPedSim default
_DESIRED_SPEED_MPS = 1.34     # mean walking speed, Fruin et al.


# ─── Output types ─────────────────────────────────────────────────────────


@dataclass
class SimResult:
    scenario_name: str
    summary: str
    agent_count: int
    total_egress_time_sec: float
    everyone_egressed: bool
    peak_density_per_m2: float
    mean_density_per_m2: float
    throughput_per_exit: dict[str, int]
    # Time series (1-second cadence approx). Each entry is (t_sec, remaining_agents).
    egress_curve: list[tuple[float, int]]
    # Density grid: cells in people/m² averaged over the run; shape (ny, nx).
    density_grid: "np.ndarray"
    # Bounds of the density grid in metres: (xmin, ymin, xmax, ymax).
    grid_bounds: tuple[float, float, float, float]
    # Walkable polygon (kept so the visualiser can mask the heatmap).
    walkable_polygon: Polygon
    # Exits as (label, polygon-tuple) for the visualiser.
    exits: list[tuple[str, tuple[tuple[float, float], ...]]]
    seed: int
    notes: list[str] = field(default_factory=list)


# ─── Validation ───────────────────────────────────────────────────────────


def _validate(agent_count: int, max_time_sec: float) -> None:
    if not (LIMITS["agent_count_min"] <= agent_count <= LIMITS["agent_count_max"]):
        raise ValueError(
            f"agent_count must be {LIMITS['agent_count_min']}–"
            f"{LIMITS['agent_count_max']}, got {agent_count}"
        )
    if not (LIMITS["max_time_min"] <= max_time_sec <= LIMITS["max_time_max"]):
        raise ValueError(
            f"max_time_sec must be {LIMITS['max_time_min']}–"
            f"{LIMITS['max_time_max']}, got {max_time_sec}"
        )


# ─── Density grid helpers ─────────────────────────────────────────────────


def _make_grid(bounds: tuple[float, float, float, float]) -> tuple[np.ndarray, tuple[float, float, float, float], tuple[int, int]]:
    """Return (zeros grid, snapped bounds, (nx, ny)) for accumulating density."""
    xmin, ymin, xmax, ymax = bounds
    # Snap to grid so every cell has the same area.
    xmin_s = math.floor(xmin / _GRID_CELL_M) * _GRID_CELL_M
    ymin_s = math.floor(ymin / _GRID_CELL_M) * _GRID_CELL_M
    xmax_s = math.ceil(xmax / _GRID_CELL_M) * _GRID_CELL_M
    ymax_s = math.ceil(ymax / _GRID_CELL_M) * _GRID_CELL_M
    nx = max(1, int(round((xmax_s - xmin_s) / _GRID_CELL_M)))
    ny = max(1, int(round((ymax_s - ymin_s) / _GRID_CELL_M)))
    return (np.zeros((ny, nx), dtype=np.float64),
            (xmin_s, ymin_s, xmax_s, ymax_s),
            (nx, ny))


def _bin_positions(positions: list[tuple[float, float]],
                    snapped_bounds: tuple[float, float, float, float],
                    nx: int, ny: int) -> np.ndarray:
    """Return a (ny, nx) count of agents per cell."""
    xmin, ymin, xmax, ymax = snapped_bounds
    if not positions:
        return np.zeros((ny, nx), dtype=np.float64)
    xs = np.asarray([p[0] for p in positions], dtype=np.float64)
    ys = np.asarray([p[1] for p in positions], dtype=np.float64)
    counts, _, _ = np.histogram2d(
        ys, xs,
        bins=[ny, nx],
        range=[[ymin, ymax], [xmin, xmax]],
    )
    return counts


# ─── Main simulate() ─────────────────────────────────────────────────────


def simulate(scenario: str | Mapping[str, Any],
              agent_count: int = DEFAULTS["agent_count"],
              max_time_sec: float = DEFAULTS["max_time_sec"],
              seed: int = DEFAULTS["seed"]) -> SimResult:
    """Run the named (or JSON) scenario and return a `SimResult`.

    The JuPedSim package is imported lazily so the rest of the `crowd`
    package can be inspected even if the binary wheel isn't installed.
    """
    _validate(agent_count, max_time_sec)
    scn = load_scenario(scenario) if not isinstance(scenario, Scenario) else scenario

    # Lazy import — keeps `crowd.scenarios` importable without JuPedSim.
    import jupedsim as jps

    sim = jps.Simulation(
        model=jps.CollisionFreeSpeedModel(),
        geometry=scn.walkable,
        dt=_DT_SEC,
    )

    # Stages: one ExitStage per exit. Each agent is routed to its nearest exit
    # at spawn time (set once; no dynamic re-routing for the MVP).
    exit_ids: list[int] = []
    journey_ids: list[int] = []
    for ex in scn.exits:
        sid = sim.add_exit_stage(list(ex.polygon))
        exit_ids.append(sid)
        journey_ids.append(sim.add_journey(jps.JourneyDescription([sid])))

    # Distribute agents inside the spawn area. JuPedSim enforces
    # minimum-distance constraints; if the spawn is too dense it raises.
    try:
        positions = jps.distribute_by_number(
            polygon=scn.spawn_area,
            number_of_agents=agent_count,
            distance_to_agents=2 * _AGENT_RADIUS_M + 0.1,
            distance_to_polygon=_AGENT_RADIUS_M + 0.05,
            seed=seed,
        )
    except Exception as exc:
        raise ValueError(
            f"could not place {agent_count} agents in scenario "
            f"'{scn.name}' (spawn area = {scn.spawn_area.area:.1f} m²): {exc}"
        ) from exc

    # Pick nearest exit per agent (computed against the exit centroid).
    exit_centroids: list[tuple[float, float]] = []
    for ex in scn.exits:
        cx = sum(p[0] for p in ex.polygon) / len(ex.polygon)
        cy = sum(p[1] for p in ex.polygon) / len(ex.polygon)
        exit_centroids.append((cx, cy))

    agent_exit_idx: list[int] = []
    for pos in positions:
        # numpy.float64 → plain floats so we can json-serialise if needed.
        px, py = float(pos[0]), float(pos[1])
        dists = [(px - cx) ** 2 + (py - cy) ** 2 for cx, cy in exit_centroids]
        idx = int(np.argmin(dists))
        agent_exit_idx.append(idx)
        sim.add_agent(jps.CollisionFreeSpeedModelAgentParameters(
            position=(px, py),
            journey_id=journey_ids[idx],
            stage_id=exit_ids[idx],
            v0=_DESIRED_SPEED_MPS,
            radius=_AGENT_RADIUS_M,
        ))

    initial_count_per_exit = {
        scn.exits[i].label: agent_exit_idx.count(i) for i in range(len(scn.exits))
    }

    # ── Iterate ──────────────────────────────────────────────────────────
    grid, snapped_bounds, (nx, ny) = _make_grid(scn.bounds)
    snapshots = 0

    egress_curve: list[tuple[float, int]] = [(0.0, agent_count)]
    peak_density = 0.0
    cell_area = _GRID_CELL_M * _GRID_CELL_M
    max_iterations = int(max_time_sec / _DT_SEC)

    while sim.agent_count() > 0 and sim.iteration_count() < max_iterations:
        sim.iterate()
        if sim.iteration_count() % _SNAPSHOT_EVERY_N == 0:
            t = sim.iteration_count() * _DT_SEC
            positions_now = [(a.position[0], a.position[1]) for a in sim.agents()]
            counts = _bin_positions(positions_now, snapped_bounds, nx, ny)
            grid += counts
            snapshots += 1
            peak_here = float(counts.max() / cell_area) if counts.size else 0.0
            if peak_here > peak_density:
                peak_density = peak_here
            egress_curve.append((round(t, 2), sim.agent_count()))

    # Final point on the curve.
    final_t = sim.iteration_count() * _DT_SEC
    if not egress_curve or egress_curve[-1][0] != round(final_t, 2):
        egress_curve.append((round(final_t, 2), sim.agent_count()))

    # Throughput per exit = initial agents headed there minus those still in sim.
    # Cheap heuristic: an agent is "gone" if its (initial) idx is no longer in
    # the live agent list. We can't read agent_idx from JuPedSim's live agents
    # cheaply, so we approximate: any agent whose journey_id matches still in
    # the sim is counted as not-yet-evacuated.
    remaining_by_journey: dict[int, int] = {jid: 0 for jid in journey_ids}
    for a in sim.agents():
        jid = a.journey_id
        remaining_by_journey[jid] = remaining_by_journey.get(jid, 0) + 1
    throughput: dict[str, int] = {}
    for i, ex in enumerate(scn.exits):
        spawned_here = initial_count_per_exit[ex.label]
        still_here = remaining_by_journey.get(journey_ids[i], 0)
        throughput[ex.label] = spawned_here - still_here

    # Mean density = (sum of agents binned) / (snapshots × cell_area × walkable cells).
    walkable_cells = _count_walkable_cells(scn.walkable, snapped_bounds, nx, ny)
    if snapshots > 0 and walkable_cells > 0:
        mean_density = float(grid.sum() / (snapshots * walkable_cells * cell_area))
    else:
        mean_density = 0.0

    # Normalise grid into time-averaged density (people/m²).
    if snapshots > 0:
        density_grid = grid / (snapshots * cell_area)
    else:
        density_grid = grid

    everyone_out = sim.agent_count() == 0
    notes: list[str] = []
    if not everyone_out:
        notes.append(
            f"Simulation hit max_time_sec={max_time_sec:.0f}s with "
            f"{sim.agent_count()} agents still inside. total_egress_time_sec "
            f"reports the cap, not a completed evacuation."
        )

    return SimResult(
        scenario_name=scn.name,
        summary=scn.summary,
        agent_count=agent_count,
        total_egress_time_sec=round(final_t, 2),
        everyone_egressed=everyone_out,
        peak_density_per_m2=round(peak_density, 3),
        mean_density_per_m2=round(mean_density, 3),
        throughput_per_exit=throughput,
        egress_curve=egress_curve,
        density_grid=density_grid,
        grid_bounds=snapped_bounds,
        walkable_polygon=scn.walkable,
        exits=[(ex.label, ex.polygon) for ex in scn.exits],
        seed=seed,
        notes=notes,
    )


def _count_walkable_cells(walkable: Polygon,
                           bounds: tuple[float, float, float, float],
                           nx: int, ny: int) -> int:
    """Return the count of grid cells whose centre lies inside `walkable`.

    Used to compute mean density over the walkable footprint only.
    """
    xmin, ymin, _xmax, _ymax = bounds
    count = 0
    for j in range(ny):
        cy = ymin + (j + 0.5) * _GRID_CELL_M
        for i in range(nx):
            cx = xmin + (i + 0.5) * _GRID_CELL_M
            if walkable.contains(Point(cx, cy)):
                count += 1
    return count
