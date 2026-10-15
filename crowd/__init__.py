"""
crowd — gaming-physics-style pedestrian dynamics for AI agents.

Wraps Forschungszentrum Jülich's JuPedSim (https://www.jupedsim.org/) so an
LLM tool call can run a pre-canned evacuation scenario and get back:

    - total egress time (s)
    - peak local density (people/m²)
    - throughput per exit
    - egress curve PNG (people remaining vs time)
    - cumulative-density heatmap PNG

The kernel is deterministic (RNG seeded), capped on agent count and
sim-time, and ships three pre-canned scenarios:

    - lobby_evacuation : 20 m × 15 m lobby, one 3 m exit, agents spawn uniformly
    - corridor_flow    : 30 m × 3 m corridor, agents flow left-to-right
    - two_exit_hall    : 25 m × 25 m hall, two 2 m exits on opposite walls

A JSON scenario dict (see crowd.scenarios.SCENARIO_JSON_SCHEMA) can also be
passed for bespoke geometries.
"""

from __future__ import annotations

from .engine import (
    DEFAULTS,
    LIMITS,
    SimResult,
    simulate,
)
from .scenarios import (
    SCENARIO_JSON_SCHEMA,
    available_scenarios,
    describe_scenario,
    load_scenario,
)

__all__ = [
    "DEFAULTS",
    "LIMITS",
    "SCENARIO_JSON_SCHEMA",
    "SimResult",
    "available_scenarios",
    "describe_scenario",
    "load_scenario",
    "simulate",
]
