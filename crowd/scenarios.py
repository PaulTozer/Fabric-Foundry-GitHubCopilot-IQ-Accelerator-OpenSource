"""Pre-canned crowd-flow scenarios + JSON-schema loader for bespoke geometries.

Each scenario is a `Scenario` describing:

    - `walkable`     : shapely Polygon — the floor-plan walkable area
    - `spawn_area`   : shapely Polygon — where agents are placed at t=0
    - `exits`        : list of `Exit` (rectangle + label)
    - `waypoints`    : optional list of `Waypoint` (x, y) — visited before the exit
    - `bounds`       : (xmin, ymin, xmax, ymax) for visualisation framing
    - `summary`      : human-readable one-line description

`load_scenario(name_or_dict)` returns one. Accepts:

    - one of the keys in `available_scenarios()` (str)
    - a dict that conforms to `SCENARIO_JSON_SCHEMA`
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from shapely.geometry import Polygon


# ─── Types ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Exit:
    """A 4-corner exit polygon. JuPedSim treats it as an absorbing region."""
    label: str
    polygon: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class Waypoint:
    """Optional intermediate goal (centre + radius)."""
    x: float
    y: float
    radius: float = 0.5


@dataclass(frozen=True)
class Scenario:
    name: str
    summary: str
    walkable: Polygon
    spawn_area: Polygon
    exits: tuple[Exit, ...]
    waypoints: tuple[Waypoint, ...] = field(default_factory=tuple)
    bounds: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)


# ─── Pre-canned scenarios ─────────────────────────────────────────────────


def _lobby_evacuation() -> Scenario:
    """20 m wide × 15 m deep lobby. One 3 m exit centred on the south wall.

    Spawn area excludes a 0.5 m boundary so agents start clear of walls.
    """
    walkable = Polygon([(0, 0), (20, 0), (20, 15), (0, 15)])
    spawn = Polygon([(0.5, 0.5), (19.5, 0.5), (19.5, 14.5), (0.5, 14.5)])
    exit_rect = ((8.5, 0.0), (11.5, 0.0), (11.5, 0.3), (8.5, 0.3))
    return Scenario(
        name="lobby_evacuation",
        summary="20m × 15m lobby with one 3m-wide exit on the south wall.",
        walkable=walkable,
        spawn_area=spawn,
        exits=(Exit("south_exit", exit_rect),),
        bounds=(-1.0, -1.0, 21.0, 16.0),
    )


def _corridor_flow() -> Scenario:
    """30 m long × 3 m wide corridor. Agents spawn on the left half and head
    to a 3 m exit on the east wall.
    """
    walkable = Polygon([(0, 0), (30, 0), (30, 3), (0, 3)])
    spawn = Polygon([(0.5, 0.5), (10.0, 0.5), (10.0, 2.5), (0.5, 2.5)])
    exit_rect = ((29.7, 0.0), (30.0, 0.0), (30.0, 3.0), (29.7, 3.0))
    return Scenario(
        name="corridor_flow",
        summary="30m × 3m corridor; agents flow east to a 3m exit.",
        walkable=walkable,
        spawn_area=spawn,
        exits=(Exit("east_exit", exit_rect),),
        bounds=(-1.0, -1.0, 31.0, 4.0),
    )


def _two_exit_hall() -> Scenario:
    """25 m × 25 m square hall with 2 m exits on the north and south walls.

    Agents are uniformly distributed and each picks the nearest exit (JuPedSim
    routes via separate journeys; we split the spawn into N/S halves at t=0).
    """
    walkable = Polygon([(0, 0), (25, 0), (25, 25), (0, 25)])
    spawn = Polygon([(0.5, 0.5), (24.5, 0.5), (24.5, 24.5), (0.5, 24.5)])
    south_exit = ((11.5, 0.0), (13.5, 0.0), (13.5, 0.3), (11.5, 0.3))
    north_exit = ((11.5, 24.7), (13.5, 24.7), (13.5, 25.0), (11.5, 25.0))
    return Scenario(
        name="two_exit_hall",
        summary="25m × 25m hall with two 2m exits (north + south walls).",
        walkable=walkable,
        spawn_area=spawn,
        exits=(Exit("south_exit", south_exit), Exit("north_exit", north_exit)),
        bounds=(-1.0, -1.0, 26.0, 26.0),
    )


_BUILTINS: dict[str, callable] = {
    "lobby_evacuation": _lobby_evacuation,
    "corridor_flow":    _corridor_flow,
    "two_exit_hall":    _two_exit_hall,
}


def available_scenarios() -> list[str]:
    """Return the names of all pre-canned scenarios."""
    return sorted(_BUILTINS.keys())


def describe_scenario(name: str) -> str:
    """Return the one-line human description for a built-in scenario."""
    if name not in _BUILTINS:
        return f"unknown scenario '{name}'"
    return _BUILTINS[name]().summary


# ─── Custom scenario loader (JSON) ────────────────────────────────────────


SCENARIO_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name":     {"type": "string"},
        "summary":  {"type": "string"},
        "walkable": {
            "type": "array",
            "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
            "minItems": 3,
            "description": "Outer walkable polygon as [[x,y], ...] in metres.",
        },
        "spawn_area": {
            "type": "array",
            "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
            "minItems": 3,
            "description": "Polygon where agents start. Must lie inside `walkable`.",
        },
        "exits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label":   {"type": "string"},
                    "polygon": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                        "minItems": 3,
                    },
                },
                "required": ["label", "polygon"],
            },
            "minItems": 1,
        },
    },
    "required": ["name", "walkable", "spawn_area", "exits"],
}


def _polygon_from_coords(coords: list[list[float]]) -> Polygon:
    """Build a shapely Polygon from a list of [x, y] pairs. Raises ValueError
    if the polygon is degenerate or invalid."""
    if not coords or len(coords) < 3:
        raise ValueError("polygon must have at least 3 vertices")
    poly = Polygon([(float(x), float(y)) for x, y in coords])
    if not poly.is_valid or poly.area <= 0:
        raise ValueError(f"polygon is invalid or zero-area: {poly.area=}")
    return poly


def _scenario_from_dict(d: Mapping[str, Any]) -> Scenario:
    """Build a Scenario from a JSON-shaped dict. Validates shape inline."""
    for key in ("name", "walkable", "spawn_area", "exits"):
        if key not in d:
            raise ValueError(f"scenario dict missing required key '{key}'")
    walkable = _polygon_from_coords(d["walkable"])
    spawn = _polygon_from_coords(d["spawn_area"])
    if not walkable.contains(spawn) and not walkable.intersection(spawn).area >= spawn.area * 0.99:
        raise ValueError("spawn_area must lie inside walkable")
    exits: list[Exit] = []
    for raw in d["exits"]:
        if "label" not in raw or "polygon" not in raw:
            raise ValueError("each exit must have 'label' and 'polygon'")
        coords = raw["polygon"]
        if len(coords) < 3:
            raise ValueError(f"exit '{raw['label']}' polygon must have >=3 vertices")
        exits.append(Exit(label=str(raw["label"]),
                           polygon=tuple((float(x), float(y)) for x, y in coords)))
    minx, miny, maxx, maxy = walkable.bounds
    pad = max(1.0, 0.05 * max(maxx - minx, maxy - miny))
    return Scenario(
        name=str(d["name"]),
        summary=str(d.get("summary") or f"custom scenario '{d['name']}'"),
        walkable=walkable,
        spawn_area=spawn,
        exits=tuple(exits),
        bounds=(minx - pad, miny - pad, maxx + pad, maxy + pad),
    )


def load_scenario(name_or_dict: str | Mapping[str, Any]) -> Scenario:
    """Resolve a scenario by built-in name or by JSON dict.

    Raises ValueError on unknown name or malformed dict.
    """
    if isinstance(name_or_dict, str):
        if name_or_dict not in _BUILTINS:
            raise ValueError(
                f"unknown scenario '{name_or_dict}'. "
                f"Known: {available_scenarios()}, or pass a JSON dict."
            )
        return _BUILTINS[name_or_dict]()
    if isinstance(name_or_dict, Mapping):
        return _scenario_from_dict(name_or_dict)
    raise TypeError(f"scenario must be str or dict, got {type(name_or_dict).__name__}")
