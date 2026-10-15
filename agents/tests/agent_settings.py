"""Validate nonsecret browser-owned agent definitions at each server boundary."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mcp_network import approved_url

ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
SERVER_ID = re.compile(r"^[a-z][a-z0-9_-]{0,33}$")
TOOL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,79}$")


def default_system_prompt() -> str:
    from copilot_runner import CopilotConfigurationError

    try:
        instructions = (Path(__file__).resolve().parents[1] / "CopilotSDK_Instructions.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise CopilotConfigurationError("The packaged enterprise analyst instructions are unavailable.") from None
    if not instructions.strip() or len(instructions) > 20000:
        raise CopilotConfigurationError("The packaged enterprise analyst instructions are invalid.")
    return instructions


def text(value: Any, label: str, limit: int, *, empty: bool = True) -> str:
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ValueError(f"Invalid {label}; maximum {limit} characters.")
    return value


def validate_agent(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) - {"id", "name", "systemPrompt", "skills", "servers"}:
        raise ValueError("Invalid agent configuration fields.")
    if not isinstance(value.get("id"), str) or not ID.fullmatch(value["id"]):
        raise ValueError("Invalid agent ID.")
    result = {"id": value["id"], "name": text(value.get("name"), "agent name", 80, empty=False),
              "systemPrompt": text(value.get("systemPrompt", ""), "system prompt", 20000),
              "skills": [], "servers": []}
    skills = value.get("skills", [])
    if not isinstance(skills, list) or len(skills) > 32:
        raise ValueError("Maximum 32 agent skills.")
    seen = set()
    for skill in skills:
        if not isinstance(skill, dict) or set(skill) - {"id", "name", "description", "prompt", "enabled"}:
            raise ValueError("Invalid skill fields.")
        sid = skill.get("id")
        if not isinstance(sid, str) or not ID.fullmatch(sid) or sid in seen:
            raise ValueError("Invalid or duplicate skill ID.")
        seen.add(sid)
        if not isinstance(skill.get("enabled"), bool):
            raise ValueError("Skill enabled must be boolean.")
        result["skills"].append({
            "id": sid, "name": text(skill.get("name"), "skill name", 80, empty=False),
            "description": text(skill.get("description", ""), "skill description", 300),
            "prompt": text(skill.get("prompt"), "skill prompt", 20000, empty=False),
            "enabled": skill["enabled"],
        })
    if sum(len(s["prompt"]) for s in result["skills"]) + len(result["systemPrompt"]) > 40000:
        raise ValueError("Combined agent prompt and skills exceed 40000 characters.")
    servers = value.get("servers", [])
    if not isinstance(servers, list) or len(servers) > 8:
        raise ValueError("Maximum 8 user MCP servers.")
    seen = set()
    for entry in servers:
        if not isinstance(entry, dict):
            raise ValueError("Invalid remote MCP definition.")
        if entry.get("type") not in {"http", "sse"}:
            raise ValueError("User MCP connections must be remote HTTP or SSE; stdio is operator-only.")
        if set(entry) - {"id", "type", "url", "enabled", "authRef", "tools"}:
            raise ValueError("Invalid MCP fields; secrets, commands and raw headers are not accepted.")
        sid = entry.get("id")
        if not isinstance(sid, str) or not SERVER_ID.fullmatch(sid) or sid in seen:
            raise ValueError("Invalid or duplicate MCP server ID.")
        seen.add(sid)
        if not isinstance(entry.get("enabled"), bool):
            raise ValueError("MCP enabled must be boolean.")
        url = text(entry.get("url"), "MCP URL", 2048, empty=False)
        auth_ref = text(entry.get("authRef", "public"), "auth reference", 64, empty=False)
        # Disabled definitions are drafts only and cannot be connected or routed.
        if entry["enabled"]:
            approved_url(url, auth_ref)
        tools = entry.get("tools", [])
        if not isinstance(tools, list) or len(tools) > 64 or any(
            not isinstance(name, str) or not TOOL_NAME.fullmatch(name) for name in tools
        ):
            raise ValueError("Invalid MCP tool allowlist.")
        result["servers"].append({"id": sid, "type": entry["type"], "url": url,
                                  "enabled": entry["enabled"], "authRef": auth_ref, "tools": tools})
    return result
