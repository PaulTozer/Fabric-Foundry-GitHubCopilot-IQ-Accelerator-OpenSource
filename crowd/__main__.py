"""CLI smoke test: `python -m crowd <scenario> [--agents 80] [--max-time 90] [--seed 1]`.

Writes egress.png + heatmap.png next to the working directory and prints a
summary line.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engine import DEFAULTS, simulate
from .scenarios import available_scenarios
from .visualize import render_density_heatmap, render_egress_curve


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m crowd")
    p.add_argument("scenario", help=f"name {available_scenarios()} or path to scenario.json")
    p.add_argument("--agents", type=int, default=DEFAULTS["agent_count"])
    p.add_argument("--max-time", type=float, default=DEFAULTS["max_time_sec"])
    p.add_argument("--seed", type=int, default=DEFAULTS["seed"])
    p.add_argument("--out", type=Path, default=Path("."),
                   help="directory to write egress.png + heatmap.png")
    args = p.parse_args(argv)

    scenario_arg: object
    src = Path(args.scenario)
    if src.suffix.lower() == ".json" and src.exists():
        scenario_arg = json.loads(src.read_text(encoding="utf-8"))
    else:
        scenario_arg = args.scenario

    result = simulate(
        scenario=scenario_arg,
        agent_count=args.agents,
        max_time_sec=args.max_time,
        seed=args.seed,
    )

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "egress.png").write_bytes(render_egress_curve(result))
    (args.out / "heatmap.png").write_bytes(render_density_heatmap(result))

    print(f"scenario:         {result.scenario_name}")
    print(f"agents:           {result.agent_count}")
    print(f"total_egress_sec: {result.total_egress_time_sec}  "
           f"(complete={result.everyone_egressed})")
    print(f"peak_density:     {result.peak_density_per_m2} p/m²")
    print(f"mean_density:     {result.mean_density_per_m2} p/m²")
    print(f"throughput:       {result.throughput_per_exit}")
    for n in result.notes:
        print(f"  note: {n}")
    print(f"wrote: {args.out / 'egress.png'}")
    print(f"wrote: {args.out / 'heatmap.png'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
