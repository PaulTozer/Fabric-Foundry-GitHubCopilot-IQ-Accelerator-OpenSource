"""Compatibility entry point: method M4 now executes GitHub Copilot SDK BYOK."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from copilot_runner import run_copilot
from fabric_tools import _FinalWrapper


def run_m4(openai: Any, case: dict, deadline: float,
           tool_call_counter: list[int], token_totals: dict, trace: list[dict],
           **options: Any) -> tuple[Any, str]:
    # The first argument remains for the existing evaluation API, not execution.
    result = asyncio.run(run_copilot(
        str(case["Question"]), timeout=max(0.01, deadline - time.time()),
        tool_call_counter=tool_call_counter, token_totals=token_totals,
        trace=trace, **options,
    ))
    return _FinalWrapper(result, None, [], None), ""
