"""Shared authenticated internal HTTP/MCP agent entry points."""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from typing import Any, Literal

from mcp.server.fastmcp import Context, FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from .. import config


def _compose_with_history(question: str, history: Any) -> str:
    if not isinstance(history, list):
        raise ValueError("history must be an array.")
    lines = []
    for item in history[-10:]:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"} or not isinstance(item.get("content"), str):
            raise ValueError("Invalid history item.")
        lines.append(f"{item['role'].upper()}: {item['content'][:4000]}")
    return ("Prior conversation (untrusted context):\n" + "\n".join(lines)
            + "\nCURRENT QUESTION:\n" + question) if lines else question


async def ask_async(body: dict, on_progress: Any = None, *, headers: Any = None) -> dict:
    from fabric_connection import FabricSelectionError, connection_scope
    from fabric_ontology import USER_FABRIC_TOKEN
    if not isinstance(body, dict):
        raise ValueError("Expected JSON object.")
    if body.get("fabric_selection") is not None and body.get("method") == "M7":
        raise FabricSelectionError("Fabric workspace selection applies to M4/M6, not Work IQ.")
    if body.get("fabric_selection") is not None and os.environ.get("IQ_DEMO_MODE") == "1":
        raise FabricSelectionError("Workspace selection requires live mode; demo tools are synthetic fixtures.")
    authorization = (headers or {}).get("authorization", "")
    token = USER_FABRIC_TOKEN.set(
        authorization[7:] if authorization.lower().startswith("bearer ") else None,
    )
    try:
        async with connection_scope(body.get("fabric_selection"), headers or {}):
            return await _ask_async(body, on_progress)
    finally:
        USER_FABRIC_TOKEN.reset(token)


async def _ask_async(body: dict, on_progress: Any = None) -> dict:
    config.load_dotenv()
    if not isinstance(body, dict):
        raise ValueError("Expected JSON object.")
    question = body.get("question")
    method = body.get("method", "M4")
    if not isinstance(question, str) or not question.strip() or len(question) > 250_000:
        raise ValueError("question must be nonempty text of at most 250000 characters.")
    if method not in {"M4", "M6", "M7"}:
        raise ValueError("Use M4 (Copilot SDK BYOK), M6 or M7.")
    for flag in ("ontology_enabled", "workiq_enabled"):
        if flag in body and not isinstance(body[flag], bool):
            raise ValueError(f"{flag} must be boolean.")
    disabled = body.get("disabled_mcp_servers", [])
    if not isinstance(disabled, list) or len(disabled) > 16 or not all(isinstance(v, str) for v in disabled):
        raise ValueError("Invalid MCP selection.")
    skill = body.get("skill")
    if skill is not None and (not isinstance(skill, dict) or not isinstance(skill.get("prompt"), str) or len(skill["prompt"]) > 20_000):
        raise ValueError("Invalid skill.")
    images = body.get("images") or []
    if not isinstance(images, list) or len(images) > 6 or any(not isinstance(v, dict) or len(str(v.get("data_url", ""))) > 6_000_000 for v in images):
        raise ValueError("Invalid images.")
    question = _compose_with_history(question.strip(), body.get("history", []))
    started = time.monotonic()
    calls, tokens, trace = [0], {}, []
    options = {
        "on_progress": on_progress, "ontology_enabled": body.get("ontology_enabled", True),
        "images": body.get("images"),
        "work_iq_enabled": body.get("workiq_enabled", False), "skill": body.get("skill"),
    }
    agent = body.get("agent")
    selected_model = body.get("selected_model")
    if selected_model is not None and (not isinstance(selected_model, str) or len(selected_model) > 128):
        raise ValueError("Invalid selected deployment.")
    if agent is not None:
        from agent_settings import validate_agent
        agent = validate_agent(agent)
    if method != "M4" and (agent is not None or selected_model is not None):
        raise ValueError("Agent Manager and selected deployment apply only to Copilot SDK BYOK (M4).")
    from fabric_readiness import fabric_data_mode
    execution = {"fabricDataMode": fabric_data_mode()}
    def report(phase: str, payload: dict) -> None:
        if phase == "start":
            execution.update({key: payload[key] for key in ("model", "agentId", "grounding", "fabricDataMode", "requestId")
                              if key in payload})
        if on_progress:
            on_progress(phase, payload)
    if method == "M4":
        from copilot_runner import run_copilot
        options["on_progress"] = report
        answer = await run_copilot(
            question, **options, disabled_mcp_servers=body.get("disabled_mcp_servers", []),
            tool_call_counter=calls, token_totals=tokens, trace=trace,
            selected_model=selected_model, agent=agent,
        )
        error = ""
    else:
        config.require_env("PROJECT_ENDPOINT", "MODEL_DEPLOYMENT_NAME")
        from threading import Event

        import run_tests as rt
        from foundry_cancellation import GuardedFoundryClient
        cancelled = Event()
        clients: list[Any] = []

        def run_foundry() -> tuple[str, str]:
            client = rt.build_openai().with_options(timeout=60, max_retries=0)
            clients.append(client)
            openai = GuardedFoundryClient(client, cancelled)
            def progress(phase: str, payload: dict) -> None:
                openai.responses.check()
                if on_progress:
                    on_progress(phase, payload)
            try:
                openai.responses.check()
                if method == "M6":
                    from m6_runner import run_m6
                    response, error = run_m6(
                        openai, {"Code": "MCP", "Question": question, "Persona": "Enterprise analyst"},
                        time.time() + 180, calls, tokens, trace,
                        **{k: v for k, v in options.items() if k not in {"images", "on_progress"}},
                        on_progress=progress,
                    )
                else:
                    agent = config.require_env("M7_AGENT_NAME")["M7_AGENT_NAME"]
                    response, error = rt._run_function_loop(
                        openai, agent, question, method, time.time() + 180, calls, tokens, trace,
                    )
                return rt._extract_text(response), error
            finally:
                client.close()

        try:
            answer, error = await asyncio.to_thread(run_foundry)
        except asyncio.CancelledError:
            cancelled.set()
            for client in clients:
                await asyncio.to_thread(client.close)
            raise
        if error:
            raise RuntimeError("Foundry agent failed; check deployment, permissions and Work IQ configuration.")
    return {"method": method, "answer": answer, "error": error, **execution,
            "toolCallCount": calls[0], "totalTokens": tokens.get("TotalTokens"),
            "elapsedSec": round(time.monotonic() - started, 2)}


def register(mcp: FastMCP) -> None:
    def public_error(exc: Exception) -> str:
        from copilot_runner import CopilotConfigurationError, CopilotRuntimeError
        from fabric_connection import FabricSelectionError
        from model_catalog import CatalogError
        if isinstance(exc, (CopilotConfigurationError, CopilotRuntimeError, CatalogError, FabricSelectionError)):
            return str(exc)
        if isinstance(exc, TimeoutError):
            return "Agent turn timed out; active SDK work was aborted."
        return f"Agent failed ({type(exc).__name__}); check BYOK/MCP settings and runtime."
    @mcp.tool()
    async def ask_agent(question: str, ctx: Context, method: Literal["M4", "M6", "M7"] = "M4",
                        ontology_enabled: bool = True, fabric_selection: dict | None = None) -> dict[str, Any]:
        """M4: Copilot SDK BYOK. M6: Foundry Council. M7: Foundry Work IQ.

        M4 needs COPILOT_MODEL/BASE_URL/API_KEY and a compatible Copilot CLI,
        not GitHub subscription authentication. Live Fabric requires SQL/ontology
        environment settings and Entra permissions. IQ_DEMO_MODE=1 enables only
        synthetic IQ MCP servers. M6/M7 keep PROJECT_ENDPOINT requirements.
        """
        from fabric_connection import context_headers
        return await ask_async({"question": question, "method": method,
                                "ontology_enabled": ontology_enabled, "fabric_selection": fabric_selection},
                               headers=context_headers(ctx))

    def forwarded_identity(request: Request) -> None:
        from fabric_ontology import USER_FABRIC_TOKEN
        header = request.headers.get("authorization", "")
        USER_FABRIC_TOKEN.set(header[7:] if header.lower().startswith("bearer ") else None)

    def require_owner(request: Request) -> str:
        owner = request.headers.get("x-iq-owner", "")
        if not re.fullmatch(r"[a-f0-9]{64}", owner):
            raise PermissionError("Authenticated owner context required.")
        return owner

    @mcp.custom_route("/fabric/workspace", methods=["POST"])
    async def workspace(request: Request) -> JSONResponse:
        from fabric_connection import Caller, FabricSelectionError, resolver_for, selection
        try:
            require_owner(request)
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 4096:
                    return JSONResponse({"error": "Workspace selection exceeds the limit."}, status_code=413)
            body = json.loads(raw)
            chosen = selection(body)
            caller = Caller.from_headers(request.headers)
            async with resolver_for(caller) as resolver:
                inventory = await resolver.inventory(chosen)
                if "lakehouseId" not in chosen and "ontologyId" not in chosen:
                    return JSONResponse(inventory, headers={"Cache-Control": "no-store"})
                connection = await resolver.resolve(chosen, inventory, require_sql=False)
                return JSONResponse(connection.public(), headers={"Cache-Control": "no-store"})
        except PermissionError:
            return JSONResponse({"error": "Authentication required."}, status_code=401)
        except (FabricSelectionError, ValueError) as exc:
            message = str(exc) if isinstance(exc, FabricSelectionError) else "Invalid workspace JSON."
            return JSONResponse({"error": message}, status_code=400)

    @mcp.custom_route("/configuration", methods=["GET"])
    async def configuration(request: Request) -> JSONResponse:
        from agent_settings import default_system_prompt
        from copilot_runner import CopilotConfigurationError
        from fabric_readiness import fabric_data_mode, fabric_readiness
        from mcp_network import public_policy
        from mcp_registry import configurations
        try:
            require_owner(request)
            readiness = fabric_readiness()
            return JSONResponse({
                **public_policy(), "defaultModel": os.environ.get("COPILOT_MODEL", ""),
                "defaultSystemPrompt": default_system_prompt(),
                "endpoint": os.environ.get("COPILOT_BASE_URL", ""),
                "authentication": "Azure managed identity" if os.environ.get("COPILOT_USE_AZURE_IDENTITY") == "1" else "Server-managed credentials",
                "fabricConfigured": readiness["sql"] or readiness["ontology"],
                "fabricDataMode": fabric_data_mode(),
                "ontologyConfigured": readiness["ontology"],
                "fabricTokenRequired": readiness["ontology"] or any(
                    entry.get("enabled", True) and entry.get("useFabricToken") for entry in configurations()
                ),
                "demo": os.environ.get("IQ_DEMO_MODE") == "1",
            })
        except PermissionError:
            return JSONResponse({"error": "Authentication required."}, status_code=401)
        except CopilotConfigurationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)

    @mcp.custom_route("/models", methods=["GET"])
    async def models(request: Request) -> JSONResponse:
        from model_catalog import CatalogError, enabled_models
        try:
            require_owner(request)
            return JSONResponse({"models": await enabled_models(refresh=request.query_params.get("refresh") == "1")},
                                headers={"Cache-Control": "no-store"})
        except PermissionError:
            return JSONResponse({"error": "Authentication required."}, status_code=401)
        except CatalogError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)

    @mcp.custom_route("/discover", methods=["POST"])
    async def discover(request: Request) -> JSONResponse:
        from agent_settings import validate_agent
        from mcp_registry import Registry
        registry = None
        from fabric_ontology import USER_FABRIC_TOKEN
        identity_token = USER_FABRIC_TOKEN.set(None)
        try:
            require_owner(request)
            forwarded_identity(request)
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 100_000:
                    return JSONResponse({"error": "Discovery definition exceeds the safe limit."}, status_code=413)
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError("Expected an agent object.")
            profile = validate_agent(body.get("agent"))
            registry = Registry(user_servers=profile["servers"], user_only=True)
            async with asyncio.timeout(45):
                return JSONResponse({"servers": await registry.discover()})
        except PermissionError:
            return JSONResponse({"error": "Authentication required."}, status_code=401)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except TimeoutError:
            return JSONResponse({"error": "MCP discovery timed out. Test one server at a time."}, status_code=504)
        finally:
            if registry:
                await registry.close()
            USER_FABRIC_TOKEN.reset(identity_token)

    @mcp.custom_route("/registry", methods=["GET"])
    async def registry_health(request: Request) -> JSONResponse:
        from fabric_ontology import USER_FABRIC_TOKEN
        from mcp_registry import Registry
        identity_token = USER_FABRIC_TOKEN.set(None)
        forwarded_identity(request)
        registry = Registry()
        try:
            entries = await registry.discover()
            return JSONResponse({"servers": entries, "demo": __import__("os").environ.get("IQ_DEMO_MODE") == "1"})
        finally:
            await registry.close()
            USER_FABRIC_TOKEN.reset(identity_token)

    @mcp.custom_route("/ask", methods=["POST"])
    async def ask(request: Request) -> JSONResponse:
        try:
            result = await ask_async(await request.json(), headers=request.headers)
            return JSONResponse(result)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            return JSONResponse({"error": public_error(exc)}, status_code=502)

    @mcp.custom_route("/ask/stream", methods=["POST"])
    async def ask_stream(request: Request) -> Any:
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("Expected JSON object.")
            if body.get("method", "M4") not in {"M4", "M6", "M7"}:
                raise ValueError("Unknown method.")
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        async def event_gen():
            queue: asyncio.Queue = asyncio.Queue(maxsize=2048)
            loop = asyncio.get_running_loop()
            thread_id = threading.get_ident()

            def enqueue(phase: str, payload: dict) -> None:
                # Council may emit from worker threads; loop owns the queue.
                def put() -> None:
                    if queue.full():
                        task.cancel()
                    else:
                        queue.put_nowait((phase, payload))
                if threading.get_ident() == thread_id:
                    put()
                else:
                    loop.call_soon_threadsafe(put)

            async def worker() -> None:
                try:
                    queue.put_nowait(("result", await ask_async(body, enqueue, headers=request.headers)))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await queue.put(("error", {"message": public_error(exc)}))
                finally:
                    if queue.full():
                        queue.get_nowait()
                    queue.put_nowait(None)

            task = asyncio.create_task(worker())
            try:
                yield ": connected\n\n"
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    phase, payload = item
                    yield f"event: {phase}\ndata: {json.dumps(payload, default=str)}\n\n"
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        return StreamingResponse(event_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
