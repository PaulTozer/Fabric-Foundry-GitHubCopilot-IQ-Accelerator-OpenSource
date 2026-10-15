"""Crowd-flow simulation tool — wraps the workspace-level `crowd` package
(JuPedSim CollisionFreeSpeedModel) as a single MCP tool."""
from __future__ import annotations

from typing import Any, Union

from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def simulate_crowd_flow(
        scenario: Union[str, dict[str, Any]] = "lobby_evacuation",
        agent_count: int = 50,
        max_time_sec: float = 120.0,
        seed: int = 1,
    ) -> dict[str, Any]:
        """Run a pedestrian-dynamics simulation and return egress time, peak
        density, throughput per exit, plus two base64-encoded PNGs (egress
        curve + time-averaged density heatmap).

        Useful for FM evacuation planning at Enterprise-managed sites — lobbies,
        retail floors, hospital concourses, station entrances, etc.

        Parameters
        ----------
        scenario : str | dict
            One of the pre-canned names — `lobby_evacuation`, `corridor_flow`,
            `two_exit_hall` — or a JSON geometry dict per `crowd.SCENARIO_JSON_SCHEMA`.
        agent_count : int        1–500 (default 50).
        max_time_sec : float     5–300 (default 120).
        seed : int               RNG seed (default 1).
        """
        try:
            from crowd import simulate  # type: ignore[import-not-found]
            from crowd.visualize import (  # type: ignore[import-not-found]
                render_density_heatmap,
                render_egress_curve,
                to_base64,
            )
        except ImportError as exc:
            return {
                "error": (
                    "crowd-flow engine not installed on this MCP server. "
                    f"Import failed: {exc}. Expected packages: jupedsim, "
                    "shapely, matplotlib, numpy."
                )
            }

        try:
            result = simulate(
                scenario=scenario,
                agent_count=int(agent_count),
                max_time_sec=float(max_time_sec),
                seed=int(seed),
            )
        except ValueError as exc:
            return {"error": f"simulation rejected: {exc}"}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"simulation failed: {type(exc).__name__}: {exc}"}

        try:
            egress_png = render_egress_curve(result)
            heatmap_png = render_density_heatmap(result)
        except Exception as exc:  # noqa: BLE001
            return {
                "scenario_name":         result.scenario_name,
                "summary":               result.summary,
                "agent_count":           result.agent_count,
                "total_egress_time_sec": result.total_egress_time_sec,
                "everyone_egressed":     result.everyone_egressed,
                "peak_density_per_m2":   result.peak_density_per_m2,
                "mean_density_per_m2":   result.mean_density_per_m2,
                "throughput_per_exit":   result.throughput_per_exit,
                "egress_curve":          result.egress_curve,
                "notes":                 result.notes + [f"viz_error: {exc}"],
            }

        return {
            "scenario_name":           result.scenario_name,
            "summary":                 result.summary,
            "agent_count":             result.agent_count,
            "total_egress_time_sec":   result.total_egress_time_sec,
            "everyone_egressed":       result.everyone_egressed,
            "peak_density_per_m2":     result.peak_density_per_m2,
            "mean_density_per_m2":     result.mean_density_per_m2,
            "throughput_per_exit":     result.throughput_per_exit,
            "egress_curve":            result.egress_curve,
            "egress_curve_png_b64":    to_base64(egress_png),
            "density_heatmap_png_b64": to_base64(heatmap_png),
            "seed":                    result.seed,
            "notes":                   result.notes,
        }
