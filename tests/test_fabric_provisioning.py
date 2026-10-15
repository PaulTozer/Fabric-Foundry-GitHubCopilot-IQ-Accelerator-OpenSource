import base64
import json
from collections import Counter
from copy import deepcopy
from types import ModuleType, SimpleNamespace

import pytest

from infra.fabric_test_provisioning import (
    Configuration,
    FabricClient,
    PreparationError,
    Response,
    build_sample_notebook,
    prepare,
)

WORKSPACE = "11111111-1111-4111-8111-111111111111"
LAKEHOUSE = "22222222-2222-4222-8222-222222222222"
NOTEBOOK = "33333333-3333-4333-8333-333333333333"
ONTOLOGY = "44444444-4444-4444-8444-444444444444"
JOB = "55555555-5555-4555-8555-555555555555"
OPERATOR = "66666666-6666-4666-8666-666666666666"
SQL = "sample.sql.example"
NOTEBOOK_RUN = f"/v1/workspaces/{WORKSPACE}/notebooks/{NOTEBOOK}/jobs/execute/instances?beta=false"


def is_notebook_run(url):
    return "/jobs/RunNotebook/instances" in url or url.endswith("/jobs/execute/instances?beta=false")


def inactive_operator_token():
    claims = base64.urlsafe_b64encode(json.dumps({"oid": OPERATOR}).encode()).decode().rstrip("=")
    return f"inactive.{claims}.not-a-signature"


class MemoryState:
    def __init__(self):
        self.value = {}
        self.writes = 0

    def load(self):
        return deepcopy(self.value)

    def save(self, value):
        self.writes += 1
        self.value = deepcopy(value)


class FakeFabric:
    def __init__(self):
        self.calls = []
        self.items = {}
        self.definitions = {}
        self.job_status = "Completed"
        self.sql_status = "Success"
        self.lro = False
        self.operation_result = {}
        self.exit_value = None
        self.generation = 1
        self.jobs = 0
        self.cancel_fails = False
        self.cancel_status = "Cancelled"
        self.roles = [{"principal": {"id": OPERATOR, "type": "User"}, "role": "Admin"}]
        self.access = [{"principal": {"id": OPERATOR, "type": "User"}, "itemAccessDetails": {
            "type": "Notebook", "permissions": ["Read", "Write", "Reshare", "Execute"],
            "additionalPermissions": []}}]

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, deepcopy(body)))
        path = url.split("/v1/", 1)[1]
        if method == "GET" and path == f"workspaces/{WORKSPACE}":
            return Response(200, {}, {"id": WORKSPACE, "capacityId": "capacity"})
        if method == "GET" and path == f"workspaces/{WORKSPACE}/items":
            return Response(200, {}, {"value": list(self.items.values())})
        if method == "GET" and path == f"workspaces/{WORKSPACE}/roleAssignments":
            return Response(200, {}, {"value": self.roles})
        if method == "GET" and path == f"admin/workspaces/{WORKSPACE}/items/{NOTEBOOK}/users":
            return Response(200, {}, {"accessDetails": self.access})
        if method == "GET" and "/tables" in path:
            return Response(200, {}, {"data": []})
        if method == "POST" and path.endswith(("/lakehouses", "/notebooks", "/ontologies")):
            kind = path.rsplit("/", 1)[1]
            item_type, item_id = {
                "lakehouses": ("Lakehouse", LAKEHOUSE),
                "notebooks": ("Notebook", NOTEBOOK),
                "ontologies": ("Ontology", ONTOLOGY),
            }[kind]
            item = {"id": item_id, "type": item_type, "workspaceId": WORKSPACE,
                    "displayName": body["displayName"], "description": body["description"]}
            if item_type == "Ontology":
                parts = body["definition"]["parts"]
                assert any(part["path"] == "definition.json" for part in parts)
                assert any(part["path"].startswith("EntityTypes/") for part in parts)
                assert not any(part["path"].endswith(".tmdl") for part in parts)
                item["properties"] = {"generation": self.generation}
            self.items[item_id] = item
            if "definition" in body:
                self.definitions[item_id] = body["definition"]
            if item_type == "Notebook":
                notebook = json.loads(base64.b64decode(body["definition"]["parts"][0]["payload"]))
                self.exit_value = f"enterprise-iq-test:v1:{WORKSPACE}:{LAKEHOUSE}:" + notebook["metadata"]["enterpriseiq_sample"]["digest"]
            if self.lro:
                self.operation_result = item
                return Response(202, {"Location": "https://api.fabric.microsoft.com/v1/operations/op",
                                      "Retry-After": "0"}, None)
            return Response(201, {}, item)
        if method == "GET" and path == "operations/op":
            return Response(200, {"Location": "https://api.fabric.microsoft.com/v1/operations/op/result"},
                            {"status": "Succeeded"})
        if method == "GET" and path == "operations/op/result":
            return Response(200, {}, self.operation_result)
        if method == "GET" and path.endswith(f"/lakehouses/{LAKEHOUSE}"):
            return Response(200, {}, {**self.items[LAKEHOUSE], "properties": {
                "sqlEndpointProperties": {"connectionString": SQL, "provisioningStatus": self.sql_status}}})
        if method == "GET" and path.endswith(f"/ontologies/{ONTOLOGY}"):
            return Response(200, {}, self.items[ONTOLOGY])
        if method == "GET" and "/items/" in path and "/jobs/" not in path:
            return Response(200, {}, self.items[path.rsplit("/", 1)[1]])
        if method == "POST" and "/getDefinition" in path:
            item_id = path.split("/getDefinition", 1)[0].rsplit("/", 1)[1]
            return Response(200, {}, {"definition": self.definitions[item_id]})
        if method == "POST" and is_notebook_run(url):
            import uuid
            self.jobs += 1
            job_id = str(uuid.UUID(int=uuid.UUID(JOB).int + self.jobs - 1))
            return Response(202, {"Location": (
                f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE}/items/{NOTEBOOK}"
                f"/jobs/instances/{job_id}"), "Retry-After": "0"}, None)
        if method == "POST" and path.endswith("/cancel"):
            if self.cancel_fails:
                return Response(403, {}, {"errorCode": "InsufficientPrivileges"})
            self.job_status = self.cancel_status
            return Response(202, {"Location": url[:-7], "Retry-After": "0"}, None)
        if method == "GET" and "/jobs/instances/" in path:
            return Response(200, {}, {"id": path.rsplit("/", 1)[1], "itemId": NOTEBOOK,
                                     "status": self.job_status, "failureReason": None})
        if method == "GET" and "/jobs/execute/instances/" in path:
            assert url.endswith("?beta=true")
            return Response(200, {}, {"id": path.rsplit("/", 1)[1].split("?")[0], "itemId": NOTEBOOK,
                                     "status": self.job_status, "failureReason": None,
                                     "exitValue": self.exit_value})
        raise AssertionError(f"Unexpected request {method} {path}")


def configuration(**values):
    return Configuration.from_env({"AZURE_ENV_NAME": "offline-feature",
                                   "FABRIC_DEPLOY_TEST_ONTOLOGY": "1",
                                   "FABRIC_TEST_WORKSPACE_ID": WORKSPACE, **values})


def client(fake):
    return FabricClient(inactive_operator_token, transport=fake, sleep=lambda _: None)


def ontology_service_readback(definition):
    result = deepcopy(definition)
    for part in result["parts"]:
        path = part["path"]
        if path in {".platform", "definition.json"}:
            continue
        value = json.loads(base64.b64decode(part["payload"]))
        if "/DataBindings/" in path:
            kind = "dataBinding"
            value["dataBindingConfiguration"]["sourceTableProperties"]["sourceSchema"] = None
        elif "/Contextualizations/" in path:
            kind = "contextualization"
            value["dataBindingTable"]["sourceSchema"] = None
        elif path.startswith("EntityTypes/"):
            kind = "entityType"
            value["untypedProperties"] = []
        else:
            kind = "relationshipType"
        value["$schema"] = f"https://developer.microsoft.com/json-schemas/fabric/item/ontology/{kind}/1.0.0/schema.json"
        part["payload"] = base64.b64encode(json.dumps(value).encode()).decode()
    return result


def test_generation_one_service_readback_defaults_preserve_exact_sample_contract():
    class ServiceReadback(FakeFabric):
        def __call__(self, method, url, headers, body):
            response = super().__call__(method, url, headers, body)
            if f"/ontologies/{ONTOLOGY}/getDefinition" in url:
                response.body["definition"] = ontology_service_readback(response.body["definition"])
            return response

    state = MemoryState()
    outputs = prepare(configuration(), client(ServiceReadback()), state)
    assert outputs["FABRIC_TEST_PROVISIONING_STATUS"] == "ready"
    assert state.value["verified"] is True


@pytest.mark.parametrize("mutation", [
    "sourceSchema", "workspaceId", "itemId", "sourceTableName", "targetPropertyId",
    "unknownExtra", "unknownSchema", "untypedProperty", "entityId", "relationshipTarget",
    "unrelatedNull", "wrongSourceType", "wrongBindingType", "contextSchema", "contextUnknownNull",
])
def test_generation_one_readback_still_rejects_binding_and_definition_drift(mutation):
    class DriftingReadback(FakeFabric):
        def __call__(self, method, url, headers, body):
            response = super().__call__(method, url, headers, body)
            if f"/ontologies/{ONTOLOGY}/getDefinition" in url:
                definition = ontology_service_readback(response.body["definition"])
                marker = (
                    "/Contextualizations/" if mutation.startswith("context")
                    else "/DataBindings/" if mutation not in {"untypedProperty", "entityId", "relationshipTarget"}
                    else "EntityTypes/" if mutation != "relationshipTarget" else "RelationshipTypes/"
                )
                part = next(part for part in definition["parts"] if marker in part["path"])
                value = json.loads(base64.b64decode(part["payload"]))
                if mutation in {"sourceSchema", "workspaceId", "itemId", "sourceTableName"}:
                    value["dataBindingConfiguration"]["sourceTableProperties"][mutation] = "changed"
                elif mutation == "targetPropertyId":
                    value["dataBindingConfiguration"]["propertyBindings"][0]["targetPropertyId"] = "changed"
                elif mutation == "unknownExtra":
                    value["unknownExtra"] = None
                elif mutation == "unknownSchema":
                    value["$schema"] = "https://schema.example/untrusted"
                elif mutation == "untypedProperty":
                    value["untypedProperties"] = [{"id": "unexpected", "name": "Extra", "valueType": "Any"}]
                elif mutation == "entityId":
                    value["id"] = "changed"
                elif mutation == "unrelatedNull":
                    value["sourceSchema"] = None
                elif mutation == "wrongSourceType":
                    value["dataBindingConfiguration"]["sourceTableProperties"]["sourceType"] = "KustoTable"
                elif mutation == "wrongBindingType":
                    value["dataBindingConfiguration"]["dataBindingType"] = "TimeSeries"
                elif mutation == "contextSchema":
                    value["dataBindingTable"]["sourceSchema"] = "dbo"
                elif mutation == "contextUnknownNull":
                    value["dataBindingTable"]["unknownExtra"] = None
                else:
                    value["target"]["entityTypeId"] = "changed"
                part["payload"] = base64.b64encode(json.dumps(value).encode()).decode()
                response.body["definition"] = definition
            return response

    state = MemoryState()
    with pytest.raises(PreparationError, match="Ontology definition/binding drift"):
        prepare(configuration(), client(DriftingReadback()), state)
    assert state.value["status"] == "failed"
    assert "outputs" not in state.value


def test_generation_one_builder_never_sends_null_optional_string_schema():
    from _create_ontology import build_ontology_parts
    parts = build_ontology_parts(WORKSPACE, LAKEHOUSE, "SyntheticSample")
    assert len(parts) == 36
    for part in parts:
        value = json.loads(base64.b64decode(part["payload"]))
        if "/DataBindings/" in part["path"]:
            assert "sourceSchema" not in value["dataBindingConfiguration"]["sourceTableProperties"]
        elif "/Contextualizations/" in part["path"]:
            assert "sourceSchema" not in value["dataBindingTable"]


@pytest.mark.parametrize("path", [None, 12, False, [], {}])
def test_generation_one_malformed_part_path_records_controlled_failure(path):
    class MalformedPath(FakeFabric):
        def __call__(self, method, url, headers, body):
            response = super().__call__(method, url, headers, body)
            if f"/ontologies/{ONTOLOGY}/getDefinition" in url:
                definition = deepcopy(response.body["definition"])
                definition["parts"][1]["path"] = path
                response.body["definition"] = definition
            return response

    state = MemoryState()
    with pytest.raises(PreparationError, match="ontology definition"):
        prepare(configuration(), client(MalformedPath()), state)
    assert state.value["status"] == "failed"
    assert "outputs" not in state.value


@pytest.mark.parametrize("flag", [None, "0"])
def test_default_zero_calls_tokens_generation_state(monkeypatch, flag):
    import infra.fabric_test_provisioning as module
    values = {"FABRIC_SQL_ENDPOINT": SQL, "FABRIC_SQL_DATABASE": "KeepDatabase",
              "FABRIC_WORKSPACE_ID": WORKSPACE, "FABRIC_LAKEHOUSE_ID": LAKEHOUSE,
              "FABRIC_ONTOLOGY_ID": ONTOLOGY}
    if flag is not None:
        values["FABRIC_DEPLOY_TEST_ONTOLOGY"] = flag
    before = deepcopy(values)
    settings = Configuration.from_env(values)
    def fail(*args, **kwargs):
        pytest.fail("Default path must have zero side effects")
    monkeypatch.setattr(module, "generate_data", fail)
    state = SimpleNamespace(load=fail, save=fail)
    assert prepare(settings, FabricClient(fail, transport=fail), state) is None
    assert values == before
    assert settings.existing["FABRIC_SQL_DATABASE"] == "KeepDatabase"


@pytest.mark.parametrize("values", [
    {}, {"FABRIC_SQL_ENDPOINT": SQL},
    {"FABRIC_WORKSPACE_ID": WORKSPACE, "FABRIC_ONTOLOGY_ID": ONTOLOGY},
    {"FABRIC_ONTOLOGY_MCP_URL": f"https://api.fabric.microsoft.com/v1/mcp/{ONTOLOGY}"},
    {"FABRIC_WORKSPACE_ID": WORKSPACE},
])
def test_supported_existing_patterns(values):
    assert not Configuration.from_env(values).deploy_test


def test_sample_rejects_demo_fixtures_without_tightening_existing_mode():
    with pytest.raises(PreparationError, match="IQ_DEMO_MODE"):
        configuration(IQ_DEMO_MODE="1")
    assert not Configuration.from_env({"IQ_DEMO_MODE": "1"}).deploy_test


@pytest.mark.parametrize("values", [
    {"FABRIC_DEPLOY_TEST_ONTOLOGY": "true"},
    {"FABRIC_WORKSPACE_ID": "not-a-uuid"},
    {"FABRIC_ONTOLOGY_ID": ONTOLOGY},
    {"FABRIC_SQL_ENDPOINT": "https://sql.example"},
    {"FABRIC_ONTOLOGY_MCP_URL": "http://api.fabric.microsoft.com/mcp"},
    {"FABRIC_DEPLOY_TEST_ONTOLOGY": "1", "AZURE_ENV_NAME": "test"},
])
def test_bad_inputs_fail_before_transport(values):
    with pytest.raises(PreparationError):
        Configuration.from_env(values)


def test_true_pipeline_uses_returned_ids_and_verified_handoff():
    fake, state = FakeFabric(), MemoryState()
    result = prepare(configuration(), client(fake), state)
    assert result["FABRIC_TEST_LAKEHOUSE_ID"] == LAKEHOUSE
    assert result["FABRIC_TEST_ONTOLOGY_ID"] == ONTOLOGY
    assert result["FABRIC_TEST_SQL_ENDPOINT"] == SQL
    assert result["FABRIC_TEST_PROVISIONING_STATUS"] == "ready"
    assert state.value["status"] == "ready"
    posts = [url for method, url, _ in fake.calls if method == "POST"]
    assert posts.index(next(url for url in posts if url.endswith("/lakehouses"))) < posts.index(
        next(url for url in posts if is_notebook_run(url))) < posts.index(
        next(url for url in posts if url.endswith("/ontologies")))
    definition = fake.definitions[ONTOLOGY]
    decoded = [json.loads(base64.b64decode(part["payload"])) for part in definition["parts"]]
    bindings = [part["dataBindingConfiguration"]["sourceTableProperties"] for part in decoded
                if "dataBindingConfiguration" in part]
    assert bindings and all(binding["workspaceId"] == WORKSPACE and binding["itemId"] == LAKEHOUSE
                            and binding["sourceTableName"].islower() for binding in bindings)


def test_small_artifacts_are_deterministic_complete_and_bounded():
    from _gen_demo_data import generate_data
    from enterprise_schema import TABLE_NAMES
    rows = generate_data(small=True)
    assert rows == generate_data(small=True)
    assert set(rows) == set(TABLE_NAMES) and all(rows.values())
    assert sum(map(len, rows.values())) <= 3000
    assert len(rows["ENT_Staff"]) == 24 and len(rows["ENT_Project"]) == 48
    notebook = build_sample_notebook(WORKSPACE, LAKEHOUSE, "SampleLake", rows)
    from infra.fabric_test_provisioning import _definition
    assert len(json.dumps(_definition(notebook)).encode()) <= 2 * 1024 * 1024
    text = json.dumps(notebook)
    assert "overwrite" not in text and "append" not in text
    assert "errorifexists" in text and "exceptAll" in text
    assert notebook["metadata"]["dependencies"]["lakehouse"]["default_lakehouse"] == LAKEHOUSE


@pytest.mark.parametrize("failure", ["Failed", "Cancelled", "Deduped", "Unexpected"])
def test_job_failure_never_creates_ontology_or_reports_ready(failure):
    fake, state = FakeFabric(), MemoryState()
    fake.job_status = failure
    with pytest.raises(PreparationError):
        prepare(configuration(), client(fake), state)
    assert ONTOLOGY not in fake.items
    assert state.value["status"] == "failed"


def test_lro_creation_resolves_actual_results():
    fake = FakeFabric()
    fake.lro = True
    assert prepare(configuration(), client(fake), MemoryState())["FABRIC_TEST_ONTOLOGY_ID"] == ONTOLOGY


def test_collision_is_not_adopted_or_overwritten():
    fake = FakeFabric()
    from infra.fabric_test_provisioning import sample_identity
    name, _ = sample_identity(configuration())
    fake.items[LAKEHOUSE] = {"id": LAKEHOUSE, "displayName": name + "_Lakehouse",
                            "type": "Lakehouse", "description": "customer data"}
    with pytest.raises(PreparationError, match="ownership|collision"):
        prepare(configuration(), client(fake), MemoryState())
    assert all(method == "GET" for method, _, _ in fake.calls)


def test_existing_description_drift_blocks_mutations():
    fake, state = FakeFabric(), MemoryState()
    prepare(configuration(), client(fake), state)
    fake.calls.clear()
    fake.items[LAKEHOUSE]["description"] = "changed-owner"
    with pytest.raises(PreparationError, match="ownership"):
        prepare(configuration(), client(fake), state)
    assert all(method == "GET" for method, _, _ in fake.calls)


def test_untrusted_pagination_or_lro_url_never_receives_token():
    calls = []
    def transport(method, url, headers, body):
        calls.append(url)
        return Response(202, {"Location": "https://untrusted.example/operation"}, None)
    api = FabricClient(lambda: "inactive", transport=transport)
    with pytest.raises(PreparationError, match="origin"):
        api.operation("POST", "/workspaces/test/ontologies", {})
    assert len(calls) == 1


def test_deadline_does_not_print_success():
    now = [0.0]
    def sleep(seconds):
        now[0] += max(seconds, 1)
    api = FabricClient(lambda: "inactive", sleep=sleep, clock=lambda: now[0],
                       transport=lambda *args: Response(200, {}, {"status": "Running"}))
    with pytest.raises(PreparationError, match="deadline"):
        api.poll("https://api.fabric.microsoft.com/v1/operations/op",
                 success="Succeeded", pending={"Running", "NotStarted"}, timeout=2)


@pytest.mark.parametrize("status", ["Failed", "Cancelled", "Canceled"])
def test_confirmed_failed_job_retry_does_not_recreate_items(status):
    fake, state = FakeFabric(), MemoryState()
    fake.job_status = status
    with pytest.raises(PreparationError):
        prepare(configuration(), client(fake), state)
    assert "job" not in state.value and state.value["last_failed_job"]["status"] == status
    assert state.value["last_failed_job"]["url"].endswith(JOB)
    fake.calls.clear()
    fake.job_status = "Completed"
    result = prepare(configuration(), client(fake), state)
    assert result["FABRIC_TEST_ONTOLOGY_ID"] == ONTOLOGY
    writes = [url for method, url, _ in fake.calls if method == "POST"]
    assert not any(url.endswith(("/lakehouses", "/notebooks")) for url in writes)
    assert sum(is_notebook_run(url) for url in writes) == 1


def test_completed_sample_rerun_verifies_with_creation_disabled():
    fake, state = FakeFabric(), MemoryState()
    prepare(configuration(), client(fake), state)
    fake.calls.clear()
    prepare(configuration(), client(fake), state)
    writes = [(url, body) for method, url, body in fake.calls if method == "POST"]
    assert not any(url.endswith(("/lakehouses", "/notebooks", "/ontologies")) for url, _ in writes)
    job = next(body for url, body in writes if is_notebook_run(url))
    assert job["parameters"] == [{"name": "allow_create", "value": False, "type": "Boolean"}]


def test_wrong_generation_cannot_publish_ready():
    fake, state = FakeFabric(), MemoryState()
    fake.generation = 2
    with pytest.raises(PreparationError, match="generation"):
        prepare(configuration(), client(fake), state)
    assert state.value["status"] == "failed"


def test_missing_verification_exit_value_does_not_create_ontology():
    fake, state = FakeFabric(), MemoryState()
    def transport(method, url, headers, body):
        response = fake(method, url, headers, body)
        if method == "GET" and "/jobs/execute/instances/" in url:
            response.body.pop("exitValue")
        return response
    with pytest.raises(PreparationError, match="exit value"):
        prepare(configuration(), client(transport), state)
    assert ONTOLOGY not in fake.items
    assert "job" in state.value


@pytest.mark.parametrize("shape", ["nested", "both", "legacy"])
def test_notebook_exit_value_matches_published_and_legacy_result_shapes(shape):
    fake, state = FakeFabric(), MemoryState()

    def transport(method, url, headers, body):
        response = fake(method, url, headers, body)
        if method == "GET" and "/jobs/execute/instances/" in url and shape != "legacy":
            response.body["properties"] = {"compute": "Spark", "exitValue": response.body["exitValue"]}
            if shape == "nested":
                response.body.pop("exitValue")
        return response

    assert prepare(configuration(), client(transport), state)["FABRIC_TEST_PROVISIONING_STATUS"] == "ready"


@pytest.mark.parametrize("properties", [
    None, [], {"compute": "Spark"}, {"compute": "Spark", "exitValue": "wrong"},
])
def test_bad_nested_exit_evidence_cannot_use_legacy_success_value(properties):
    fake, state = FakeFabric(), MemoryState()

    def transport(method, url, headers, body):
        response = fake(method, url, headers, body)
        if method == "GET" and "/jobs/execute/instances/" in url:
            response.body["properties"] = properties
        return response

    with pytest.raises(PreparationError, match="properties|exit value"):
        prepare(configuration(), client(transport), state)
    assert ONTOLOGY not in fake.items and "job" in state.value


def test_legacy_default_image_deploy_and_strict_changed_config():
    from infra.fabric_test_provisioning import validate_deployed
    for values in ({}, {"FABRIC_SQL_ENDPOINT": SQL},
                   {"FABRIC_WORKSPACE_ID": WORKSPACE, "FABRIC_ONTOLOGY_ID": ONTOLOGY}):
        settings = Configuration.from_env(values)
        validate_deployed(settings, values, settings.existing)
        with pytest.raises(PreparationError, match="changed"):
            validate_deployed(settings, {**values, "FABRIC_ACTIVE_DATA_MODE": "existing"}, settings.existing)


def test_ready_selection_and_turning_flag_off_preserve_original_refs():
    from infra.fabric_test_provisioning import validate_ready
    fake, state = FakeFabric(), MemoryState()
    generated = prepare(configuration(), client(fake), state)
    selected = validate_ready(configuration(), generated, state.value)
    assert selected["FABRIC_LAKEHOUSE_ID"] == LAKEHOUSE
    original = {"FABRIC_WORKSPACE_ID": WORKSPACE, "FABRIC_ONTOLOGY_ID": ONTOLOGY,
                "FABRIC_SQL_ENDPOINT": "original.sql.example", "FABRIC_SQL_DATABASE": "Original"}
    disabled = Configuration.from_env({**original, **generated, "FABRIC_DEPLOY_TEST_ONTOLOGY": "0"})
    assert validate_ready(disabled, {**original, **generated}, state.value) == disabled.existing
    assert disabled.existing["FABRIC_SQL_ENDPOINT"] == "original.sql.example"
    with pytest.raises(PreparationError, match="differ"):
        validate_ready(configuration(), {**generated, "FABRIC_TEST_SQL_ENDPOINT": "changed.sql.example"}, state.value)


class FakeFrame:
    def __init__(self, spark, rows, schema):
        self.spark, self.rows, self.schema = spark, rows, schema

    def count(self):
        return len(self.rows)

    def exceptAll(self, other):
        counts = Counter(json.dumps(row, sort_keys=True) for row in self.rows)
        counts.subtract(Counter(json.dumps(row, sort_keys=True) for row in other.rows))
        rows = [json.loads(row) for row, count in counts.items() for _ in range(max(count, 0))]
        return FakeFrame(self.spark, rows, self.schema)

    def limit(self, count):
        return FakeFrame(self.spark, self.rows[:count], self.schema)

    @property
    def write(self):
        return self

    def mode(self, mode):
        assert mode == "errorifexists"
        return self

    def format(self, format_name):
        assert format_name == "delta"
        return self

    def saveAsTable(self, name):
        if name in self.spark.unregistered_paths:
            raise RuntimeError("Existing unregistered table storage; no overwrite")
        if self.spark.race:
            self.spark.tables[name] = FakeFrame(self.spark, [{"external": "synthetic"}], self.schema)
        if name in self.spark.tables:
            raise RuntimeError("Existing table raced; no overwrite")
        self.spark.tables[name] = FakeFrame(self.spark, deepcopy(self.rows), self.schema)
        self.spark.writes.append(name)


class FakeSpark:
    def __init__(self):
        self.tables, self.writes, self.race = {}, [], False
        self.unregistered_paths = set()
        self.catalog = SimpleNamespace(
            listTables=lambda: [SimpleNamespace(name=name) for name in self.tables],
            tableExists=lambda name: name in self.tables,
        )
        self.sparkContext = SimpleNamespace(parallelize=lambda rows: rows)
        self.read = SimpleNamespace(schema=lambda schema: SimpleNamespace(
            json=lambda rows: FakeFrame(self, [json.loads(row) for row in rows], schema),
        ))

    def table(self, name):
        return self.tables[name]


def execute_notebook(monkeypatch, spark, *, allow_create=None, notebook=None):
    from _gen_demo_data import generate_data
    if notebook is None:
        notebook = build_sample_notebook(WORKSPACE, LAKEHOUSE, "SampleLake", generate_data(small=True))
    exits = []
    utils = ModuleType("notebookutils")
    utils.mssparkutils = SimpleNamespace(notebook=SimpleNamespace(exit=exits.append))
    monkeypatch.setitem(__import__("sys").modules, "notebookutils", utils)
    namespace = {"spark": spark}
    exec(compile("".join(notebook["cells"][0]["source"]), "<sample-parameters>", "exec"), namespace)
    if allow_create is not None:
        namespace["allow_create"] = allow_create
    exec(compile("".join(notebook["cells"][1]["source"]), "<sample-verification>", "exec"), namespace)
    assert exits == [f"enterprise-iq-test:v1:{WORKSPACE}:{LAKEHOUSE}:" + notebook["metadata"]["enterpriseiq_sample"]["digest"]]


class ExecutingFabric(FakeFabric):
    def __init__(self, monkeypatch):
        super().__init__()
        self.monkeypatch = monkeypatch
        self.spark = FakeSpark()
        self.execution_error = None

    def __call__(self, method, url, headers, body):
        if method == "POST" and is_notebook_run(url):
            notebook = json.loads(base64.b64decode(self.definitions[NOTEBOOK]["parts"][0]["payload"]))
            override = None
            if url.endswith(NOTEBOOK_RUN):
                assert body["executionData"] == {
                    "compute": "Spark",
                    "computeConfiguration": {"defaultLakehouse": {
                        "referenceType": "ById", "itemId": LAKEHOUSE, "workspaceId": WORKSPACE,
                    }},
                }
                assert notebook["cells"][0]["metadata"]["tags"] == ["parameters"]
                parameter, = body["parameters"]
                assert parameter["name"] == "allow_create" and parameter["type"] == "Boolean"
                assert type(parameter["value"]) is bool
                override = parameter["value"]
            try:
                execute_notebook(self.monkeypatch, self.spark, allow_create=override, notebook=notebook)
            except RuntimeError as error:
                self.execution_error = str(error)
                self.job_status = "Failed"
            else:
                self.execution_error = None
                self.job_status = "Completed"
        response = super().__call__(method, url, headers, body)
        if method == "GET" and "/jobs/execute/instances/" in url:
            response.body["properties"] = {"compute": "Spark", "exitValue": response.body.pop("exitValue")}
        return response


def test_generic_item_job_reproduces_ignored_creation_parameter(monkeypatch):
    from _gen_demo_data import generate_data
    from infra.fabric_test_provisioning import _definition
    fake = ExecutingFabric(monkeypatch)
    fake.definitions[NOTEBOOK] = _definition(
        build_sample_notebook(WORKSPACE, LAKEHOUSE, "SampleLake", generate_data(small=True)),
    )
    fake("POST", f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE}/items/{NOTEBOOK}"
         "/jobs/RunNotebook/instances", {}, {
             "parameters": [{"name": "allow_create", "value": True, "type": "Boolean"}],
         })
    assert fake.execution_error == "Completed sample table is missing: ent_staff"
    assert fake.job_status == "Failed" and not fake.spark.writes


def test_prepare_release_job_injects_creation_flag_and_repeat_is_read_only(monkeypatch):
    from enterprise_schema import TABLE_NAMES
    fake, state = ExecutingFabric(monkeypatch), MemoryState()
    outputs = prepare(configuration(), client(fake), state)
    assert outputs["FABRIC_TEST_LAKEHOUSE_ID"] == LAKEHOUSE
    assert set(fake.spark.writes) == {name.lower() for name in TABLE_NAMES}
    assert sum(frame.count() for frame in fake.spark.tables.values()) == 1263
    submitted = [(url, body) for method, url, body in fake.calls if method == "POST" and is_notebook_run(url)]
    assert submitted[0][0].endswith(NOTEBOOK_RUN)
    assert submitted[0][1]["parameters"][0]["value"] is True
    fake.spark.writes.clear()
    prepare(configuration(), client(fake), state)
    assert not fake.spark.writes and fake.execution_error is None
    submitted = [(url, body) for method, url, body in fake.calls if method == "POST" and is_notebook_run(url)]
    assert len(submitted) == 2 and submitted[-1][0].endswith(NOTEBOOK_RUN)
    assert submitted[-1][1]["parameters"][0]["value"] is False


def test_verified_release_job_does_not_recreate_a_missing_table(monkeypatch):
    fake, state = ExecutingFabric(monkeypatch), MemoryState()
    prepare(configuration(), client(fake), state)
    fake.spark.tables.pop("ent_staff")
    fake.spark.writes.clear()
    with pytest.raises(PreparationError, match="terminal status Failed"):
        prepare(configuration(), client(fake), state)
    assert fake.execution_error == "Completed sample table is missing: ent_staff"
    assert not fake.spark.writes and state.value["status"] == "failed"


def test_initial_release_job_preserves_matching_partial_tables(monkeypatch):
    fake, state = ExecutingFabric(monkeypatch), MemoryState()
    execute_notebook(monkeypatch, fake.spark, allow_create=True)
    missing = "ent_staff"
    fake.spark.tables.pop(missing)
    preserved = {name: deepcopy(frame.rows) for name, frame in fake.spark.tables.items()}
    fake.spark.writes.clear()
    prepare(configuration(), client(fake), state)
    assert fake.spark.writes == [missing]
    assert all(frame.rows == preserved[name] for name, frame in fake.spark.tables.items() if name != missing)
    assert sum(frame.count() for frame in fake.spark.tables.values()) == 1263


def test_initial_release_job_cannot_overwrite_unregistered_storage(monkeypatch):
    fake, state = ExecutingFabric(monkeypatch), MemoryState()
    fake.spark.unregistered_paths.add("ent_staff")
    with pytest.raises(PreparationError, match="terminal status Failed"):
        prepare(configuration(), client(fake), state)
    assert fake.execution_error == "Existing unregistered table storage; no overwrite"
    assert not fake.spark.writes and fake.spark.unregistered_paths == {"ent_staff"}
    assert ONTOLOGY not in fake.items


def test_generated_notebook_creates_once_and_verifies_without_writes(monkeypatch):
    from enterprise_schema import TABLE_NAMES
    spark = FakeSpark()
    execute_notebook(monkeypatch, spark, allow_create=True)
    assert set(spark.writes) == {name.lower() for name in TABLE_NAMES}
    assert sum(table.count() for table in spark.tables.values()) == 1263
    spark.writes.clear()
    execute_notebook(monkeypatch, spark)
    assert not spark.writes


@pytest.mark.parametrize("drift", ["missing", "schema", "count", "content", "unknown"])
def test_generated_notebook_rejects_drift_without_reseeding(monkeypatch, drift):
    spark = FakeSpark()
    execute_notebook(monkeypatch, spark, allow_create=True)
    first = next(iter(spark.tables))
    if drift == "missing":
        spark.tables.pop(first)
    elif drift == "schema":
        spark.tables[first].schema = "changed-schema"
    elif drift == "count":
        spark.tables[first].rows.pop()
    elif drift == "content":
        spark.tables[first].rows[0]["synthetic_changed"] = True
    else:
        spark.tables["external_table"] = spark.tables[first]
    before = deepcopy({name: frame.rows for name, frame in spark.tables.items()})
    spark.writes.clear()
    with pytest.raises(RuntimeError, match="missing|drift|Unexpected"):
        execute_notebook(monkeypatch, spark)
    assert not spark.writes
    assert before == {name: frame.rows for name, frame in spark.tables.items()}


def test_generated_notebook_defaults_no_create_and_race_never_overwrites(monkeypatch):
    spark = FakeSpark()
    with pytest.raises(RuntimeError, match="missing"):
        execute_notebook(monkeypatch, spark)
    assert not spark.writes and not spark.tables
    spark.race = True
    with pytest.raises(RuntimeError, match="raced"):
        execute_notebook(monkeypatch, spark, allow_create=True)
    assert not spark.writes and next(iter(spark.tables.values())).rows == [{"external": "synthetic"}]


def test_sample_limits_measure_rows_and_encoded_definition():
    import random

    from _gen_demo_data import generate_data
    data = generate_data(small=True)
    data["ENT_Staff"] *= 200
    with pytest.raises(PreparationError, match="3000"):
        build_sample_notebook(WORKSPACE, LAKEHOUSE, "Sample", data)
    data = generate_data(small=True)
    data["ENT_Staff"][0]["Name"] = base64.b64encode(random.Random(7).randbytes(2 * 1024 * 1024)).decode()
    with pytest.raises(PreparationError, match="2 MiB"):
        build_sample_notebook(WORKSPACE, LAKEHOUSE, "Sample", data)


@pytest.mark.parametrize("mode", ["Prepare", "Validate", "ValidateDeployed"])
@pytest.mark.parametrize("flag", [None, "0"])
def test_default_cli_never_constructs_api_or_state(monkeypatch, capsys, mode, flag):
    import os
    import sys

    import infra.fabric_test_provisioning as module
    for name in list(os.environ):
        if name.startswith("FABRIC_") or name == "AZURE_ENV_NAME":
            monkeypatch.delenv(name)
    if flag is not None:
        monkeypatch.setenv("FABRIC_DEPLOY_TEST_ONTOLOGY", flag)
    monkeypatch.setattr(sys, "argv", ["fabric_test_provisioning.py", "--mode", mode])
    def fail(*args, **kwargs):
        pytest.fail("Default CLI must have no sample side effects")
    for name in ("FabricClient", "FileState", "generate_data", "cli_token"):
        monkeypatch.setattr(module, name, fail)
    module.main()
    assert json.loads(capsys.readouterr().out) == {"status": "unconfigured", "outputs": {}}


@pytest.mark.parametrize("failure", ["Unexpected", "Deduped"])
def test_uncertain_job_preserved_and_resume_never_submits_twice(failure):
    fake, state, now = FakeFabric(), MemoryState(), [0.0]
    fake.job_status = failure
    def sleep(seconds):
        now[0] += 1801
    api = FabricClient(inactive_operator_token, transport=fake, clock=lambda: now[0], sleep=sleep)
    with pytest.raises(PreparationError):
        prepare(configuration(), api, state)
    assert state.value["job"].endswith(JOB)
    fake.calls.clear()
    fake.job_status = "Completed"
    prepare(configuration(), client(fake), state)
    assert not any(method == "POST" and is_notebook_run(url) for method, url, _ in fake.calls)


@pytest.mark.parametrize("cancel_fails", [False, True])
@pytest.mark.parametrize("cancel_status", ["Cancelled", "Canceled"])
def test_job_deadline_cancels_only_owned_instance_and_records_outcome(cancel_fails, cancel_status):
    fake, state, now = FakeFabric(), MemoryState(), [0.0]
    fake.job_status, fake.cancel_fails = "Running", cancel_fails
    fake.cancel_status = cancel_status
    def sleep(seconds):
        now[0] += 1801 if now[0] == 0 else seconds
    api = FabricClient(inactive_operator_token, transport=fake, clock=lambda: now[0], sleep=sleep)
    with pytest.raises(PreparationError, match="cancellation"):
        prepare(configuration(), api, state)
    cancellations = [url for method, url, _ in fake.calls if method == "POST" and url.endswith("/cancel")]
    assert cancellations == [f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE}/items/{NOTEBOOK}/jobs/instances/{JOB}/cancel"]
    assert state.value["status"] == "failed"
    assert state.value["cancellation"]["status"] == ("unconfirmed" if cancel_fails else "confirmed")
    assert ("job" in state.value) == cancel_fails
    if not cancel_fails:
        assert state.value["last_failed_job"]["status"] == cancel_status


def test_ambiguous_job_submission_never_blindly_retries():
    fake, state = FakeFabric(), MemoryState()
    def transport(method, url, headers, body):
        if method == "POST" and is_notebook_run(url):
            raise PreparationError("Ambiguous transport acceptance")
        return fake(method, url, headers, body)
    with pytest.raises(PreparationError, match="Ambiguous"):
        prepare(configuration(), client(transport), state)
    assert state.value["job_intent"] and "job" not in state.value
    fake.calls.clear()
    with pytest.raises(PreparationError, match="Ambiguous notebook"):
        prepare(configuration(), client(fake), state)
    assert not any(method == "POST" and is_notebook_run(url) for method, url, _ in fake.calls)


def test_wrong_generation_reuse_and_state_version_fail_closed():
    fake, state = FakeFabric(), MemoryState()
    prepare(configuration(), client(fake), state)
    fake.items[ONTOLOGY]["properties"]["generation"] = 2
    with pytest.raises(PreparationError, match="generation"):
        prepare(configuration(), client(fake), state)
    assert state.value["status"] == "failed"
    fake.calls.clear()
    state.value["version"] = "unknown"
    with pytest.raises(PreparationError, match="ownership/version"):
        prepare(configuration(), client(fake), state)
    assert not fake.calls


@pytest.mark.parametrize("status", ["Failed", "Unknown"])
def test_sql_readiness_failure_never_publishes_ready(status):
    fake, state = FakeFabric(), MemoryState()
    fake.sql_status = status
    with pytest.raises(PreparationError, match="SQL"):
        prepare(configuration(), client(fake), state)
    assert state.value["status"] == "failed" and "outputs" not in state.value


def test_invalid_lro_and_pagination_never_leave_selected_origin():
    calls = []
    def transport(method, url, headers, body):
        calls.append(url)
        return Response(202, {"Location": f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE}"}, {})
    api = client(transport)
    with pytest.raises(PreparationError, match="operation-state"):
        api.operation("POST", "/workspaces/test/lakehouses", {})
    assert len(calls) == 1
    calls.clear()
    def paginate(method, url, headers, body):
        calls.append(url)
        return Response(200, {}, {"value": [], "continuationUri":
            f"https://foreign.example/v1/workspaces/{WORKSPACE}/items"})
    with pytest.raises(PreparationError, match="origin"):
        client(paginate).items(WORKSPACE)
    assert len(calls) == 1


def test_read_refresh_retry_bounds_and_write_error_redaction():
    responses = iter([Response(401, {}, {}), Response(429, {"Retry-After": "0"}, {}),
                      Response(503, {}, {}), Response(200, {}, {"ok": True})])
    tokens = []
    def token():
        tokens.append(True)
        return "inactive"
    api = FabricClient(token, transport=lambda *args: next(responses), sleep=lambda _: None)
    assert api.request("GET", "/workspaces/test").body == {"ok": True}
    assert len(tokens) == 2
    calls = []
    def denied(*args):
        calls.append(True)
        return Response(503, {}, {"errorCode": "Bad-Code!synthetic-sensitive", "message": "synthetic-sensitive"})
    with pytest.raises(PreparationError) as failure:
        client(denied).request("POST", "/workspaces/test/lakehouses", {})
    assert len(calls) == 1 and "synthetic-sensitive" not in str(failure.value)
    calls.clear()
    def throttled(*args):
        calls.append(True)
        return Response(429, {}, {})
    with pytest.raises(PreparationError):
        client(throttled).request("GET", "/workspaces/test")
    assert len(calls) == 6


def test_file_state_uses_exclusive_temporary_files_and_preserves_existing_temp(tmp_path):
    from infra.fabric_test_provisioning import FileState
    legacy_temp = tmp_path / "fabric-test-state.tmp"
    legacy_temp.write_text("do-not-truncate", encoding="utf-8")
    store = FileState(tmp_path / "fabric-test-state.json")
    with store.locked():
        store.save({"status": "preparing"})
        store.save({"status": "ready"})
    assert store.load() == {"status": "ready"}
    assert legacy_temp.read_text(encoding="utf-8") == "do-not-truncate"
    assert not list(tmp_path.glob("fabric-test-state-*.tmp"))
    assert not store.path.with_suffix(".lock").exists()


def test_file_state_rejects_link_or_reparse_ancestors(monkeypatch, tmp_path):
    from pathlib import Path

    from infra.fabric_test_provisioning import FileState
    original = Path.lstat
    def lstat(path):
        if path == tmp_path:
            return SimpleNamespace(st_mode=0, st_file_attributes=1024)
        return original(path)
    monkeypatch.setattr(Path, "lstat", lstat)
    store = FileState(tmp_path / "fabric-test-state.json")
    for action in (store.load, lambda: store.save({"status": "ready"})):
        with pytest.raises(PreparationError, match="link/reparse"):
            action()
    assert not store.path.exists()


@pytest.mark.parametrize("role,principal_type", [("Contributor", "User"), ("Admin", "Group"), ("Unknown", "User")])
def test_other_group_or_unknown_workspace_writers_block_before_fabric_creations(role, principal_type):
    fake, state = FakeFabric(), MemoryState()
    fake.roles.append({"role": role, "principal": {"id": JOB, "type": principal_type}})
    with pytest.raises(PreparationError, match="writer|unknown"):
        prepare(configuration(), client(fake), state)
    assert all(method == "GET" for method, _, _ in fake.calls)


@pytest.mark.parametrize("permission,additional", [("Write", []), ("Reshare", []), ("Unknown", []),
                                                  ("Read", ["unknownWorkload"])])
def test_unverified_direct_notebook_permissions_never_submit_a_job(permission, additional):
    fake, state = FakeFabric(), MemoryState()
    fake.access.append({"principal": {"id": JOB, "type": "Group"}, "itemAccessDetails": {
        "type": "Notebook", "permissions": [permission], "additionalPermissions": additional}})
    with pytest.raises(PreparationError, match="unsupported|unknown"):
        prepare(configuration(), client(fake), state)
    assert not any(method == "POST" and is_notebook_run(url) for method, url, _ in fake.calls)
    assert ONTOLOGY not in fake.items


def test_effective_writer_inventory_failure_or_absence_does_not_fallback():
    fake, state = FakeFabric(), MemoryState()
    fake.access = []
    with pytest.raises(PreparationError, match="exclusive"):
        prepare(configuration(), client(fake), state)
    assert not any(method == "POST" and is_notebook_run(url) for method, url, _ in fake.calls)
    def denied(method, url, headers, body):
        if "/admin/" in url:
            return Response(403, {}, {"errorCode": "InsufficientPrivileges"})
        return fake(method, url, headers, body)
    with pytest.raises(PreparationError, match="403"):
        prepare(configuration(), client(denied), state)
    assert state.value["status"] == "failed"


def test_role_inventory_pagination_cannot_hide_later_writer():
    fake, state, pages = FakeFabric(), MemoryState(), []
    def transport(method, url, headers, body):
        if "/roleAssignments" in url:
            pages.append(url)
            if "continuationToken" not in url:
                return Response(200, {}, {"value": fake.roles, "continuationToken": "next",
                    "continuationUri": f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE}/roleAssignments?continuationToken=next"})
            return Response(200, {}, {"value": [{"role": "Member", "principal": {"id": JOB, "type": "User"}}]})
        return fake(method, url, headers, body)
    with pytest.raises(PreparationError, match="another"):
        prepare(configuration(), client(transport), state)
    assert len(pages) == 2 and not fake.items


def test_notebook_integrity_recheck_and_operator_refresh_are_fail_closed():
    fake, state, definitions = FakeFabric(), MemoryState(), [0]
    def transport(method, url, headers, body):
        response = fake(method, url, headers, body)
        if "/notebooks/" in url and "/getDefinition" in url:
            definitions[0] += 1
            if definitions[0] == 2:
                changed = deepcopy(response.body)
                content = json.loads(base64.b64decode(changed["definition"]["parts"][0]["payload"]))
                content["cells"][0]["source"] = ["allow_create = True\n"]
                changed["definition"]["parts"][0]["payload"] = base64.b64encode(json.dumps(content).encode()).decode()
                return Response(200, {}, changed)
        return response
    with pytest.raises(PreparationError, match="immediately"):
        prepare(configuration(), client(transport), state)
    assert not any(method == "POST" and is_notebook_run(url) for method, url, _ in fake.calls)
    api = client(fake)
    api.workspace_writers(WORKSPACE)
    api.bearer = ""
    other = base64.urlsafe_b64encode(json.dumps({"oid": JOB}).encode()).decode().rstrip("=")
    api.token = lambda: f"inactive.{other}.not-a-signature"
    with pytest.raises(PreparationError, match="operator changed"):
        api.request("POST", f"/workspaces/{WORKSPACE}/lakehouses", {})


@pytest.mark.parametrize("persisted", ["job", "operation"])
def test_resource_specific_resume_urls_are_guarded(persisted):
    fake, state = FakeFabric(), MemoryState()
    fake.job_status = "Unexpected"
    with pytest.raises(PreparationError):
        prepare(configuration(), client(fake), state)
    if persisted == "job":
        state.value["job"] = f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE}/items/{ONTOLOGY}/jobs/instances/{JOB}"
    else:
        state.value["items"].pop("Lakehouse")
        state.value["pending"] = {"kind": "Lakehouse",
                                 "operation": f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE}"}
    fake.calls.clear()
    with pytest.raises(PreparationError, match="outside|operation-state"):
        prepare(configuration(), client(fake), state)
    assert not any(method == "GET" and "/jobs/instances/" in url for method, url, _ in fake.calls)


def test_query_bearing_lro_is_rejected_before_polling():
    def fake(*args):
        return Response(202, {"Location": "https://api.fabric.microsoft.com/v1/operations/op?unsupported=x"}, None)
    with pytest.raises(PreparationError, match="operation-state"):
        client(fake).operation("POST", "/workspaces/test/lakehouses", {})


def test_regional_lro_response_is_canonicalized_without_forwarding_credentials():
    operation = f"https://api.fabric.microsoft.com/v1/operations/{JOB}"
    regional = f"https://wabi-example-a-primary-redirect.analysis.windows.net/v1/operations/{JOB}"
    calls, accepted = [], []

    def transport(method, url, headers, body):
        calls.append(url)
        if method == "POST":
            return Response(202, {"Location": regional, "Retry-After": "0"}, None)
        if url == operation:
            return Response(200, {}, {"status": "Succeeded"})
        assert url == operation + "/result"
        return Response(200, {}, {"definition": {"parts": []}})

    api = client(transport)
    assert api.operation("POST", "/workspaces/test/notebooks", accepted=accepted.append) == {
        "definition": {"parts": []}
    }
    assert accepted == [operation]
    assert calls[1:] == [operation, operation + "/result"]
    with pytest.raises(PreparationError, match="origin"):
        api.operation_url(regional)
    with pytest.raises(PreparationError, match="origin"):
        api.job_resource(WORKSPACE, NOTEBOOK, regional)


@pytest.mark.parametrize("location", [
    f"http://wabi-example-redirect.analysis.windows.net/v1/operations/{JOB}",
    f"https://foreign.example/v1/operations/{JOB}",
    f"https://wabi-example-redirect.analysis.windows.net.evil.example/v1/operations/{JOB}",
    f"https://user@wabi-example-redirect.analysis.windows.net/v1/operations/{JOB}",
    f"https://wabi-example-redirect.analysis.windows.net:443/v1/operations/{JOB}",
    "https://wabi-example-redirect.analysis.windows.net/v1/operations/not-a-uuid",
    f"https://wabi-example-redirect.analysis.windows.net/v1/operations/{JOB}/result",
    f"https://wabi-example-redirect.analysis.windows.net/v1/operations/{JOB}?",
    f"https://wabi-example-redirect.analysis.windows.net/v1/operations/{JOB}#",
    f"https://wabi-example-redirect.analysis.windows.net/v1/workspaces/{WORKSPACE}",
])
def test_untrusted_regional_response_is_rejected_before_acceptance_or_polling(location):
    calls, accepted = [], []

    def transport(method, url, headers, body):
        calls.append(url)
        return Response(202, {"Location": location}, None)

    with pytest.raises(PreparationError):
        client(transport).operation("POST", "/workspaces/test/notebooks", accepted=accepted.append)
    assert len(calls) == 1
    assert not accepted


def test_observed_reshare_spelling_preserves_exclusive_delegator_guard():
    fake = FakeFabric()
    fake.access[0]["itemAccessDetails"]["permissions"] = ["Read", "Write", "ReShare", "Execute"]
    client(fake).notebook_writers(WORKSPACE, NOTEBOOK)
    fake.access.append({
        "principal": {"id": ONTOLOGY, "type": "Group"},
        "itemAccessDetails": {"type": "Notebook", "permissions": ["Read", "ReShare"],
                              "additionalPermissions": []},
    })
    with pytest.raises(PreparationError, match="writer or delegator"):
        client(fake).notebook_writers(WORKSPACE, NOTEBOOK)


@pytest.mark.parametrize("linked", [True, False])
def test_resume_excludes_only_proven_automatic_lakehouse_sql_child(linked):
    fake, state = FakeFabric(), MemoryState()
    prepare(configuration(), client(fake), state)
    child = "88888888-8888-4888-8888-888888888888"
    fake.items[child] = {
        "id": child, "type": "SQLEndpoint", "workspaceId": WORKSPACE,
        "displayName": fake.items[LAKEHOUSE]["displayName"],
    }

    def transport(method, url, headers, body):
        response = fake(method, url, headers, body)
        if method == "GET" and url.endswith(f"/lakehouses/{LAKEHOUSE}"):
            response.body["properties"]["sqlEndpointProperties"]["id"] = child if linked else JOB
        return response

    if linked:
        assert prepare(configuration(), client(transport), state)["FABRIC_TEST_LAKEHOUSE_ID"] == LAKEHOUSE
    else:
        with pytest.raises(PreparationError, match="collision"):
            prepare(configuration(), client(transport), state)


@pytest.mark.parametrize("field", ["record", "sql"])
@pytest.mark.parametrize("invalid", [None, 123, {"unexpected": "id"}])
def test_malformed_lakehouse_child_ids_fail_with_persisted_failure(field, invalid):
    fake, state = FakeFabric(), MemoryState()
    prepare(configuration(), client(fake), state)
    child = "88888888-8888-4888-8888-888888888888"
    fake.items[child] = {
        "id": child, "type": "SQLEndpoint", "workspaceId": WORKSPACE,
        "displayName": fake.items[LAKEHOUSE]["displayName"],
    }
    if field == "record":
        state.value["items"]["Lakehouse"]["id"] = invalid
    fake.calls.clear()

    def transport(method, url, headers, body):
        response = fake(method, url, headers, body)
        if method == "GET" and url.endswith(f"/lakehouses/{LAKEHOUSE}"):
            response.body["properties"]["sqlEndpointProperties"]["id"] = invalid
        return response

    with pytest.raises(PreparationError, match="supported UUID"):
        prepare(configuration(), client(transport), state)
    assert state.value["status"] == "failed"
    assert not any(method == "POST" for method, _, _ in fake.calls)


@pytest.mark.parametrize("mode,owner", [(0o040700, 123), (0o040770, 123), (0o040700, 456)])
def test_posix_state_permissions_are_operator_private(monkeypatch, tmp_path, mode, owner):
    from pathlib import Path

    import infra.fabric_test_provisioning as module
    original = Path.lstat
    def lstat(path):
        if path == tmp_path:
            return SimpleNamespace(st_mode=mode, st_uid=owner, st_file_attributes=0)
        return original(path)
    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(module, "os", SimpleNamespace(name="posix", geteuid=lambda: 123))
    store = module.FileState(tmp_path / "fabric-test-state.json")
    if mode == 0o040700 and owner == 123:
        store.trusted_path()
    else:
        with pytest.raises(PreparationError, match="operator-owned"):
            store.trusted_path()


def test_posix_symlink_metadata_is_rejected(monkeypatch, tmp_path):
    import stat
    from pathlib import Path

    from infra.fabric_test_provisioning import FileState
    original = Path.lstat
    def lstat(path):
        if path == tmp_path:
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        return original(path)
    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(PreparationError, match="link/reparse"):
        FileState(tmp_path / "fabric-test-state.json").load()


def test_no_partial_ready_handoff_and_sample_mode_cannot_use_legacy_output_exception():
    from infra.fabric_test_provisioning import validate_deployed, validate_ready
    fake, state = FakeFabric(), MemoryState()
    outputs = prepare(configuration(), client(fake), state)
    selected = validate_ready(configuration(), outputs, state.value)
    with pytest.raises(PreparationError, match="changed"):
        validate_deployed(configuration(), outputs, selected)
    active = {f"FABRIC_ACTIVE_{key.removeprefix('FABRIC_')}": value for key, value in selected.items()}
    active["FABRIC_ACTIVE_DATA_MODE"] = "test-sample"
    validate_deployed(configuration(), {**outputs, **active}, selected)
    incomplete = deepcopy(state.value)
    incomplete["outputs"].pop("FABRIC_TEST_SCHEMA_VERSION")
    with pytest.raises(PreparationError, match="incomplete"):
        validate_ready(configuration(), outputs, incomplete)


@pytest.mark.parametrize("suffix", ["?", "#", "?x=y"])
def test_empty_or_nonempty_resource_delimiters_are_rejected(suffix):
    api = client(FakeFabric())
    with pytest.raises(PreparationError):
        api.operation_url("https://api.fabric.microsoft.com/v1/operations/op" + suffix)
    with pytest.raises(PreparationError):
        api.job_resource(WORKSPACE, NOTEBOOK,
            f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE}/items/{NOTEBOOK}/jobs/instances/{JOB}" + suffix)


def test_get_retry_backoff_cannot_consume_cancellation_reserve():
    fake, state, now = FakeFabric(), MemoryState(), [0.0]
    fake.job_status = "Running"
    def transport(method, url, headers, body):
        if method == "GET" and "/jobs/instances/" in url and fake.job_status == "Running":
            return Response(503, {"Retry-After": "90"}, {})
        return fake(method, url, headers, body)
    def sleep(seconds):
        now[0] += seconds
    api = FabricClient(inactive_operator_token, transport=transport, clock=lambda: now[0], sleep=sleep)
    now[0] = 2500
    with pytest.raises(PreparationError, match="cancellation confirmed"):
        prepare(configuration(), api, state)
    assert now[0] < 2580
    assert any(method == "POST" and url.endswith(f"/{JOB}/cancel") for method, url, _ in fake.calls)
    assert state.value["cancellation"]["status"] == "confirmed"


def test_insufficient_remaining_budget_blocks_new_job_before_intent():
    fake, state, now = FakeFabric(), MemoryState(), [0.0]
    api = FabricClient(inactive_operator_token, transport=fake, clock=lambda: now[0], sleep=lambda _: None)
    now[0] = 2551
    with pytest.raises(PreparationError, match="before notebook submission"):
        prepare(configuration(), api, state)
    assert "job_intent" not in state.value
    assert not any(method == "POST" and is_notebook_run(url) for method, url, _ in fake.calls)


def test_real_transport_receives_stage_timeout_budget(monkeypatch):
    import infra.fabric_test_provisioning as module
    recorded = []
    def transport(method, url, headers, body, *, timeout):
        recorded.append(timeout)
        return Response(200, {}, {})
    monkeypatch.setattr(module, "http_transport", transport)
    api = FabricClient(lambda: "inactive", transport=transport, clock=lambda: 0.0)
    api.request("GET", "/workspaces/test", deadline=2)
    assert recorded == [2]


@pytest.mark.parametrize("stage", ["mkdir", "mkstemp", "replace", "cleanup", "replace-and-cleanup"])
def test_state_setup_persistence_and_cleanup_errors_are_safe(monkeypatch, tmp_path, stage):
    from pathlib import Path

    import infra.fabric_test_provisioning as module
    store = module.FileState(tmp_path / "fabric-test-state.json")
    mkdir, replace, unlink = Path.mkdir, Path.replace, Path.unlink
    def failed_mkdir(path, *args, **kwargs):
        if path == tmp_path and stage == "mkdir":
            raise OSError("synthetic-private-path")
        return mkdir(path, *args, **kwargs)
    def failed_temp(*args, **kwargs):
        raise OSError("synthetic-private-path")
    def failed_replace(path, *args, **kwargs):
        if stage in {"replace", "replace-and-cleanup"}:
            raise OSError("synthetic-private-path")
        return replace(path, *args, **kwargs)
    def failed_unlink(path, *args, **kwargs):
        if path.name.startswith("fabric-test-state-") and stage in {"cleanup", "replace-and-cleanup"}:
            raise OSError("synthetic-private-path")
        return unlink(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "mkdir", failed_mkdir)
        patch.setattr(Path, "replace", failed_replace)
        patch.setattr(Path, "unlink", failed_unlink)
        if stage == "mkstemp":
            patch.setattr(module.tempfile, "mkstemp", failed_temp)
        with pytest.raises(PreparationError) as failure:
            store.save({"status": "ready"})
        assert "synthetic-private-path" not in str(failure.value)
        if stage == "replace-and-cleanup":
            assert "cannot be persisted" in str(failure.value) and "cleanup failed" in str(failure.value)
    for path in tmp_path.glob("fabric-test-state-*.tmp"):
        path.unlink()


def test_uppercase_selected_workspace_prepares_same_canonical_resource(monkeypatch):
    import sys
    lowercase = "aaaaaaaa-1111-4111-8111-111111111111"
    monkeypatch.setattr(sys.modules[__name__], "WORKSPACE", lowercase)
    outputs = prepare(configuration(FABRIC_TEST_WORKSPACE_ID=lowercase.upper()), client(FakeFabric()), MemoryState())
    assert outputs["FABRIC_TEST_WORKSPACE_ID"] == lowercase


@pytest.mark.parametrize("target", ["lakehouses", "jobs/execute/instances?beta=false"])
def test_confirmed_write_rejection_can_retry_without_ambiguous_adoption(target):
    fake, state = FakeFabric(), MemoryState()
    def rejected(method, url, headers, body):
        if method == "POST" and url.endswith("/" + target):
            return Response(403, {}, {"errorCode": "InsufficientPrivileges"})
        return fake(method, url, headers, body)
    with pytest.raises(PreparationError, match="403"):
        prepare(configuration(), client(rejected), state)
    assert "pending" not in state.value and "job_intent" not in state.value
    assert prepare(configuration(), client(fake), state)["FABRIC_TEST_PROVISIONING_STATUS"] == "ready"


def test_read_transport_failure_at_reserved_expiry_still_attempts_owned_cancel():
    fake, state, now = FakeFabric(), MemoryState(), [0.0]
    fake.job_status = "Running"
    def transport(method, url, headers, body):
        if method == "GET" and "/jobs/instances/" in url and fake.job_status == "Running":
            now[0] = 2580
            raise PreparationError("Fabric transport failed")
        return fake(method, url, headers, body)
    def sleep(seconds):
        now[0] += seconds
    api = FabricClient(inactive_operator_token, transport=transport, clock=lambda: now[0], sleep=sleep)
    now[0] = 2500
    with pytest.raises(PreparationError, match="cancellation confirmed"):
        prepare(configuration(), api, state)
    assert any(method == "POST" and url.endswith(f"/{JOB}/cancel") for method, url, _ in fake.calls)
    assert state.value["cancellation"]["status"] == "confirmed"


def test_predeadline_transport_failure_and_expired_post_remain_ambiguous():
    now = [0.0]
    def fail(method, url, headers, body):
        now[0] = 2
        raise PreparationError("Fabric transport failed")
    for method, deadline in (("GET", 3), ("POST", 1)):
        now[0] = 0
        api = FabricClient(lambda: "inactive", transport=fail, clock=lambda: now[0])
        with pytest.raises(PreparationError, match="Fabric transport failed"):
            api.request(method, "/workspaces/test", deadline=deadline)
