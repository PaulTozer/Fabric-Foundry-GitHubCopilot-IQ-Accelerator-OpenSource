"""Safe deployment inventory for one operator-configured Foundry resource."""
from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any

import httpx
from azure.core.exceptions import AzureError
from azure.identity.aio import DefaultAzureCredential, ManagedIdentityCredential

RESOURCE = re.compile(
    r"^/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[A-Za-z0-9_.()-]+"
    r"/providers/Microsoft\.CognitiveServices/accounts/[A-Za-z0-9_.-]+$",
    re.IGNORECASE,
)
_cache: tuple[str, float, list[dict]] | None = None
_lock = asyncio.Lock()


class CatalogError(ValueError):
    """Static diagnostic safe to expose to the authenticated caller."""


def compatible_deployments(entries: list[dict], wire_api: str) -> list[dict]:
    result = []
    for entry in entries:
        props = entry.get("properties", {})
        model = props.get("model", {})
        capabilities = props.get("capabilities", {})
        enabled = props.get("provisioningState") == "Succeeded"
        chat = str(capabilities.get("chatCompletion", "")).lower() == "true"
        wire = wire_api != "responses" or str(capabilities.get("responses", "")).lower() == "true"
        capacity = entry.get("sku", {}).get("capacity")
        if enabled and chat and wire and model.get("format") == "OpenAI" and capacity != 0:
            name = entry.get("name")
            if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name):
                result.append({"id": name, "model": str(model.get("name", ""))[:128],
                               "version": str(model.get("version", ""))[:128]})
    return sorted(result, key=lambda item: item["id"])


def validate_selection(selection: str, deployments: list[dict]) -> str:
    if not any(item["id"] == selection for item in deployments):
        raise CatalogError("Selected model is no longer enabled or compatible. Refresh enabled models and choose a deployment.")
    return selection


async def enabled_models(*, refresh: bool = False) -> list[dict]:
    global _cache
    resource = os.environ.get("COPILOT_AZURE_RESOURCE_ID", "")
    if not RESOURCE.fullmatch(resource):
        raise CatalogError("Enabled model discovery requires the operator's COPILOT_AZURE_RESOURCE_ID. No model list was substituted.")
    wire = os.environ.get("COPILOT_WIRE_API", "completions")
    cache_key = f"{resource}:{wire}"
    async with _lock:
        if not refresh and _cache and _cache[0] == cache_key and time.monotonic() - _cache[1] < 30:
            return [dict(item) for item in _cache[2]]
        credential: Any = (
            ManagedIdentityCredential(client_id=os.environ.get("AZURE_CLIENT_ID") or None)
            if (os.environ.get("COPILOT_USE_AZURE_IDENTITY") == "1"
                or os.environ.get("IQ_ENVIRONMENT") == "production"
                or os.environ.get("IDENTITY_ENDPOINT") or os.environ.get("MSI_ENDPOINT"))
            else DefaultAzureCredential()
        )
        root = f"https://management.azure.com{resource}/deployments"
        try:
            async with credential:
                token = await credential.get_token("https://management.azure.com/.default")
            async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                entries = []
                url = f"{root}?api-version=2024-10-01"
                for _ in range(8):
                    response = await client.get(url, headers={"Authorization": f"Bearer {token.token}"})
                    if response.status_code in {401, 403}:
                        raise CatalogError("Model inventory permission denied. The MCP identity needs deployments/read on the configured model resource.")
                    if response.status_code != 200:
                        raise CatalogError("Enabled model inventory is unavailable. Check the configured resource and ARM connectivity.")
                    if len(response.content) > 2_000_000:
                        raise CatalogError("Model inventory response exceeds the safe limit.")
                    data = response.json()
                    if not isinstance(data, dict) or not isinstance(data.get("value"), list):
                        raise CatalogError("Model inventory returned an invalid deployment list.")
                    entries.extend(data["value"])
                    next_link = data.get("nextLink")
                    if not next_link:
                        break
                    if not isinstance(next_link, str) or not next_link.startswith(root + "?"):
                        raise CatalogError("Model inventory pagination escaped the approved resource.")
                    url = next_link
                else:
                    raise CatalogError("Model inventory exceeded the page limit.")
            result = compatible_deployments(entries, wire)
        except (AzureError, httpx.HTTPError):
            raise CatalogError("Enabled model discovery failed. Check identity, resource permission and ARM connectivity.") from None
        except (TypeError, KeyError, AttributeError, ValueError) as exc:
            if isinstance(exc, CatalogError):
                raise
            raise CatalogError("Enabled model inventory returned invalid metadata.") from None
        _cache = (cache_key, time.monotonic(), result)
        return [dict(item) for item in result]
