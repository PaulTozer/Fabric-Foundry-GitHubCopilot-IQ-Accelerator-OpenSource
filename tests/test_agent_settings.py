import pytest
from agent_settings import default_system_prompt, validate_agent


def agent(**updates):
    return {"id": "generic", "name": "Generic Assistant", "systemPrompt": "",
            "skills": [], "servers": [], **updates}


def test_agent_config_has_no_synthetic_or_shell_default():
    assert validate_agent(agent())["servers"] == []
    with pytest.raises(ValueError, match="remote"):
        validate_agent(agent(servers=[{"id": "shell", "type": "stdio", "command": "cmd.exe"}]))


def test_prompt_skill_bounds_and_disabled_skill_state():
    value = validate_agent(agent(systemPrompt="Be concise", skills=[
        {"id": "brief", "name": "Brief", "description": "", "prompt": "Use short sentences",
         "enabled": False},
    ]))
    assert value["skills"][0]["enabled"] is False
    with pytest.raises(ValueError, match="prompt"):
        validate_agent(agent(systemPrompt="x" * 20001))
    with pytest.raises(ValueError):
        validate_agent(agent(skills=[{"id": "brief", "name": "Brief", "prompt": "x", "enabled": "yes"}]))


def test_remote_config_rejects_raw_secrets_and_unknown_fields():
    with pytest.raises(ValueError, match="secret|field"):
        validate_agent(agent(servers=[{"id": "real", "type": "http", "url": "https://mcp.example/mcp",
                                      "enabled": True, "headers": {"Authorization": "secret"}}]))
    with pytest.raises(ValueError):
        validate_agent(agent(servers=[{"id": "real", "type": "http", "url": "https://mcp.example/mcp",
                                      "enabled": True, "authRef": "unknown"}]))


def test_domain_asset_is_read_from_packaged_docker_layout_not_working_directory(tmp_path):
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    prompt_path = "agents/CopilotSDK_Instructions.md"
    dockerfile = (root / "mcp" / "Dockerfile").read_text(encoding="utf-8")
    assert f"COPY {prompt_path} ./agents/CopilotSDK_Instructions.md" in dockerfile
    assert "COPY agents/tests/         ./agents/tests/" in dockerfile
    assert f"!{prompt_path}" in (root / ".dockerignore").read_text(encoding="utf-8").splitlines()
    shutil.copytree(root / "agents" / "tests", tmp_path / "agents" / "tests",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(root / prompt_path, tmp_path / prompt_path)
    process = subprocess.run([
        sys.executable, "-c",
        "import sys; sys.path.insert(0, 'agents/tests'); "
        "from agent_settings import default_system_prompt; print(default_system_prompt(), end='')",
    ], cwd=tmp_path, check=True, capture_output=True, text=True)
    assert process.stdout == default_system_prompt()
    assert "You are an enterprise consultancy analyst." in process.stdout


@pytest.mark.parametrize("content", ["", "x" * 20001])
def test_invalid_packaged_prompt_never_falls_back_to_generic(monkeypatch, content):
    from pathlib import Path

    from copilot_runner import CopilotConfigurationError
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: content)
    with pytest.raises(CopilotConfigurationError, match="packaged.*invalid"):
        default_system_prompt()
