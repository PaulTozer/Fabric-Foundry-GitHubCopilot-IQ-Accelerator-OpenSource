from types import SimpleNamespace

import httpx
import model_catalog
import pytest
from model_catalog import compatible_deployments, validate_selection


def deployment(name, state="Succeeded", wire="true", model_format="OpenAI", capacity=1):
    return {"name": name, "sku": {"capacity": capacity}, "properties": {
        "provisioningState": state, "model": {"name": "actual-model", "version": "version",
                                            "format": model_format},
        "capabilities": {"chatCompletion": "true", "responses": wire},
    }}


def test_enabled_catalog_uses_deployment_ids_and_compatible_wire_only():
    result = compatible_deployments([
        deployment("custom-deployment"), deployment("disabled", state="Disabled"),
        deployment("embedding", wire="false"), deployment("failed", state="Failed"),
        deployment("no-capacity", capacity=0), deployment("wrong-wire", model_format="Other"),
    ], "responses")
    assert result == [{"id": "custom-deployment", "model": "actual-model", "version": "version"}]


def test_empty_and_removed_selection_are_explicit_without_fallback():
    assert compatible_deployments([], "responses") == []
    assert validate_selection("custom-deployment", [{"id": "custom-deployment"}]) == "custom-deployment"
    with pytest.raises(ValueError, match="no longer enabled"):
        validate_selection("removed", [{"id": "different"}])


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["pagination", "json", "metadata", "permission", "escape", "empty"])
async def test_inventory_pagination_failures_empty_and_cloud_identity(monkeypatch, case):
    resource = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/models/providers/Microsoft.CognitiveServices/accounts/models"
    root = f"https://management.azure.com{resource}/deployments"
    seen, closed = [], []
    class Identity:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            closed.append(True)
        async def get_token(self, scope):
            assert scope == "https://management.azure.com/.default"
            return SimpleNamespace(token="test-inventory-token")
    def forbidden():
        pytest.fail("Hosted inventory must not fall back to developer credentials.")
    def respond(request):
        seen.append(str(request.url))
        assert request.url.host == "management.azure.com"
        if case == "json":
            return httpx.Response(200, content=b"not JSON")
        if case == "metadata":
            return httpx.Response(200, json={"value": [None]})
        if case == "permission":
            return httpx.Response(403)
        if case == "escape":
            return httpx.Response(200, json={"value": [], "nextLink": "https://evil.example/"})
        if case == "empty":
            return httpx.Response(200, json={"value": []})
        return httpx.Response(200, json={"value": [deployment(f"page-{len(seen)}")], **(
            {"nextLink": root + "?api-version=2024-10-01&skip=1"} if len(seen) == 1 else {}
        )})
    original_client = httpx.AsyncClient
    monkeypatch.setattr(model_catalog, "_cache", None)
    monkeypatch.setenv("COPILOT_AZURE_RESOURCE_ID", resource)
    monkeypatch.setenv("IQ_ENVIRONMENT", "production")
    monkeypatch.setenv("COPILOT_USE_AZURE_IDENTITY", "0")
    monkeypatch.setattr(model_catalog, "ManagedIdentityCredential", Identity)
    monkeypatch.setattr(model_catalog, "DefaultAzureCredential", forbidden)
    monkeypatch.setattr(model_catalog.httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    if case in {"pagination", "empty"}:
        result = await model_catalog.enabled_models(refresh=True)
        assert [item["id"] for item in result] == (["page-1", "page-2"] if case == "pagination" else [])
    else:
        with pytest.raises(model_catalog.CatalogError):
            await model_catalog.enabled_models(refresh=True)
    assert closed == [True] and len(seen) == (2 if case == "pagination" else 1)
