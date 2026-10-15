"""Opt-in, deployment-operator preparation of dedicated synthetic Fabric items."""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from _create_ontology import build_ontology_parts  # noqa: E402
from _gen_demo_data import generate_data  # noqa: E402
from enterprise_schema import TABLE_NAMES, TABLES  # noqa: E402

API = "https://api.fabric.microsoft.com/v1"
VERSION = "v1"
KEYS = ("WORKSPACE_ID", "LAKEHOUSE_ID", "ONTOLOGY_ID", "SQL_ENDPOINT",
        "SQL_DATABASE", "ONTOLOGY_MCP_URL")
ENV_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,63}\Z")
HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\Z")


class PreparationError(RuntimeError):
    """Safe, setting/stage-specific error with no service bodies or credentials."""


class TerminalFailure(PreparationError):
    def __init__(self, status: str):
        self.status = status
        super().__init__(f"Fabric operation reached confirmed terminal status {status}.")


class DeadlineExceeded(PreparationError):
    pass


class RequestRejected(PreparationError):
    pass


def _object(value: object, stage: str) -> dict:
    if not isinstance(value, dict):
        raise PreparationError(f"{stage}: expected a JSON object.")
    return value


def _uuid(value: str, setting: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise PreparationError(f"{setting}: expected an explicit UUID.") from None
    if str(parsed) != value.lower():
        raise PreparationError(f"{setting}: expected a hyphenated UUID.")
    return value


def _hostname(value: str, setting: str) -> str:
    if not HOST.fullmatch(value) or "." not in value or ".." in value:
        raise PreparationError(f"{setting}: expected a SQL hostname without scheme, port or path.")
    return value


@dataclass(frozen=True)
class Configuration:
    deploy_test: bool
    environment: str
    workspace: str
    existing: dict[str, str]

    @classmethod
    def from_env(cls, values: Mapping[str, str]) -> Configuration:
        flag = values.get("FABRIC_DEPLOY_TEST_ONTOLOGY", "0")
        if flag not in {"0", "1"}:
            raise PreparationError("FABRIC_DEPLOY_TEST_ONTOLOGY must be 0 or 1 (default 0).")
        existing = {f"FABRIC_{key}": values.get(f"FABRIC_{key}", "") for key in KEYS}
        existing["FABRIC_SQL_DATABASE"] = values.get("FABRIC_SQL_DATABASE", "EnterpriseKG_Lakehouse")
        for name in ("WORKSPACE_ID", "LAKEHOUSE_ID", "ONTOLOGY_ID"):
            value = existing[f"FABRIC_{name}"]
            if value:
                _uuid(value, f"FABRIC_{name}")
        if existing["FABRIC_SQL_ENDPOINT"]:
            _hostname(existing["FABRIC_SQL_ENDPOINT"], "FABRIC_SQL_ENDPOINT")
            if not existing["FABRIC_SQL_DATABASE"].strip():
                raise PreparationError("FABRIC_SQL_DATABASE is required with FABRIC_SQL_ENDPOINT.")
        url = existing["FABRIC_ONTOLOGY_MCP_URL"]
        if url:
            parsed = urllib.parse.urlsplit(url)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                    or parsed.fragment):
                raise PreparationError("FABRIC_ONTOLOGY_MCP_URL must be an explicit HTTPS URL without credentials.")
        if not existing["FABRIC_WORKSPACE_ID"] and (
            existing["FABRIC_LAKEHOUSE_ID"] or (existing["FABRIC_ONTOLOGY_ID"] and not url)
        ):
            raise PreparationError("FABRIC_WORKSPACE_ID is required for the configured Fabric item IDs.")
        environment = values.get("AZURE_ENV_NAME", "")
        workspace = values.get("FABRIC_TEST_WORKSPACE_ID", "")
        if flag == "1":
            if values.get("IQ_DEMO_MODE", "0") != "0":
                raise PreparationError("IQ_DEMO_MODE must remain 0 for persisted Fabric samples, not synthetic MCP fixtures.")
            if not ENV_NAME.fullmatch(environment):
                raise PreparationError("AZURE_ENV_NAME must be a safe environment name of at most 64 characters.")
            workspace = _uuid(workspace, "FABRIC_TEST_WORKSPACE_ID").lower()
        return cls(flag == "1", environment, workspace, existing)


def sample_identity(settings: Configuration) -> tuple[str, str]:
    scope = f"{settings.environment}/{settings.workspace.lower()}/{VERSION}"
    suffix = hashlib.sha256(scope.encode()).hexdigest()[:12]
    return f"EnterpriseIQ_Test_{suffix}_{VERSION}", suffix


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _verification_value(workspace: str, lakehouse: str, digest: str) -> str:
    return f"enterprise-iq-test:{VERSION}:{workspace.lower()}:{lakehouse.lower()}:{digest}"


def _cell(source: str, *, parameters: bool = False) -> dict:
    return {"cell_type": "code", "metadata": {"tags": ["parameters"]} if parameters else {},
            "source": source.splitlines(keepends=True), "outputs": [], "execution_count": None}


def build_sample_notebook(workspace: str, lakehouse: str, name: str, data: dict) -> dict:
    if set(data) != set(TABLE_NAMES) or any(not rows for rows in data.values()):
        raise PreparationError("Sample data must contain all twelve nonempty canonical tables.")
    if sum(len(rows) for rows in data.values()) > 3000:
        raise PreparationError("Sample data exceeds the 3000-row deployment bound.")
    digest = _digest(data)
    payload = base64.b64encode(gzip.compress(json.dumps(data).encode(), mtime=0)).decode()
    schema = {table.lower(): ", ".join(f"`{column}` {kind}" for column, kind in columns)
              for table, columns in TABLES}
    source = f"""import base64, gzip, json
rows_by_table = json.loads(gzip.decompress(base64.b64decode({payload!r})))
schemas = {schema!r}
actual_names = {{table.name.lower() for table in spark.catalog.listTables()}}
if actual_names - set(schemas):
    raise RuntimeError("Unexpected tables in dedicated sample lakehouse")
for table_name, rows in rows_by_table.items():
    name = table_name.lower()
    expected = spark.read.schema(schemas[name]).json(
        spark.sparkContext.parallelize([json.dumps(row) for row in rows])
    )
    if not spark.catalog.tableExists(name):
        if not allow_create:
            raise RuntimeError("Completed sample table is missing: " + name)
        expected.write.mode("errorifexists").format("delta").saveAsTable(name)
    actual = spark.table(name)
    if actual.schema != expected.schema or actual.limit(len(rows) + 1).count() != len(rows):
        raise RuntimeError("Sample schema or row-count drift: " + name)
    if actual.exceptAll(expected).limit(1).count() or expected.exceptAll(actual).limit(1).count():
        raise RuntimeError("Sample content drift: " + name)
from notebookutils import mssparkutils
mssparkutils.notebook.exit({_verification_value(workspace, lakehouse, digest)!r})
"""
    notebook = {
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {
            "kernel_info": {"name": "synapse_pyspark"},
            "kernelspec": {"name": "synapse_pyspark", "display_name": "Synapse PySpark"},
            "language_info": {"name": "python"},
            "enterpriseiq_sample": {"version": VERSION, "digest": digest},
            "dependencies": {"lakehouse": {"default_lakehouse": lakehouse,
                                          "default_lakehouse_name": name,
                                          "default_lakehouse_workspace_id": workspace}},
        },
        "cells": [_cell("allow_create = False\n", parameters=True), _cell(source)],
    }
    encoded = base64.b64encode(json.dumps(notebook).encode())
    if len(json.dumps({"format": "ipynb", "parts": [{"path": "notebook-content.ipynb",
                         "payload": encoded.decode(), "payloadType": "InlineBase64"}]}).encode()) > 2 * 1024 * 1024:
        raise PreparationError("Sample notebook exceeds the 2 MiB deployment bound.")
    return notebook


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: object

    def header(self, name: str) -> str:
        return next((value for key, value in self.headers.items() if key.lower() == name.lower()), "")


class Transport(Protocol):
    def __call__(self, method: str, url: str, headers: dict[str, str], body: object) -> Response: ...


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PreparationError("Fabric API redirect refused.")


def http_transport(method: str, url: str, headers: dict[str, str], body: object, *, timeout: float = 30) -> Response:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        try:
            response = urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise PreparationError("Fabric API response exceeds the 4 MiB bound.")
            try:
                value = json.loads(raw) if raw else None
            except (ValueError, UnicodeDecodeError):
                raise PreparationError("Fabric API returned invalid JSON.") from None
            return Response(response.code, dict(response.headers), value)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise PreparationError("Fabric transport failed; write acceptance may be ambiguous. Resume saved state.") from None


class FabricClient:
    def __init__(
        self, token: Callable[[], str], *, transport: Transport = http_transport,
        sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
    ):
        self.token = token
        self.transport = transport
        self.sleep = sleep
        self.clock = clock
        self.bearer = ""
        self.expected_subject = ""
        self.deadline = clock() + 45 * 60

    def subject(self) -> str:
        try:
            parts = self.bearer.split(".")
            if len(parts) != 3:
                raise ValueError
            claims = _object(json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))), "Operator claims")
            subject = claims.get("oid")
            if not isinstance(subject, str):
                raise ValueError
            return _uuid(subject, "Operator object ID").lower()
        except (ValueError, TypeError, PreparationError):
            raise PreparationError("Cannot identify the Fabric-validated operator for exclusive sample access.") from None

    def url(self, value: str) -> str:
        url = API + value if value.startswith("/") else value
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc != "api.fabric.microsoft.com"
                or not parsed.path.startswith("/v1/") or parsed.fragment
                or ".." in parsed.path.split("/")):
            raise PreparationError("Fabric API URL origin/path is not trusted.")
        return url

    def wait(self, response: Response, deadline: float) -> None:
        try:
            seconds = max(1, int(response.header("Retry-After") or "5"))
        except ValueError:
            raise PreparationError("Fabric API returned invalid Retry-After.") from None
        if self.clock() + seconds >= min(deadline, self.deadline):
            raise DeadlineExceeded("Fabric preparation deadline exhausted.")
        self.sleep(seconds)

    def request(self, method: str, url: str, body: object = None, *, deadline: float | None = None) -> Response:
        url = self.url(url)
        deadline = min(deadline if deadline is not None else self.deadline, self.deadline)
        refreshed = False
        for attempt in range(6):
            if self.clock() >= deadline:
                raise DeadlineExceeded("Fabric preparation deadline exhausted.")
            try:
                if not self.bearer:
                    self.bearer = self.token()
                    if not self.bearer:
                        raise PreparationError("Fabric operator token is missing; authenticate to the intended tenant.")
                if self.expected_subject and self.subject() != self.expected_subject:
                    raise PreparationError("Fabric operator changed during preparation; stop before further requests.")
                if self.clock() >= deadline:
                    raise DeadlineExceeded("Fabric preparation deadline exhausted.")
                headers = {
                    "Authorization": f"Bearer {self.bearer}", "Content-Type": "application/json",
                }
                if self.transport is http_transport:
                    response = http_transport(method, url, headers, body, timeout=min(30, deadline - self.clock()))
                else:
                    response = self.transport(method, url, headers, body)
            except PreparationError:
                if method == "GET" and self.clock() >= deadline:
                    raise DeadlineExceeded("Fabric read reached its stage deadline; reconcile the owned job.") from None
                raise
            if self.clock() >= deadline:
                raise DeadlineExceeded("Fabric request exceeded its stage deadline; reconcile accepted writes.")
            if response.status == 401 and method == "GET" and not refreshed:
                self.bearer = ""
                refreshed = True
                continue
            if response.status == 429 or (method == "GET" and response.status in {502, 503, 504}):
                if attempt < 5:
                    self.wait(response, deadline)
                    continue
            if response.status not in {200, 201, 202}:
                code = _object(response.body, "Fabric error").get("errorCode", "UnknownError")
                safe_code = code if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_]{1,80}", code) else "UnknownError"
                error_type = RequestRejected if method == "POST" and response.status in {
                    400, 401, 403, 404, 405, 409, 412, 422, 429,
                } else PreparationError
                raise error_type(f"Fabric {method}: HTTP {response.status} ({safe_code}); no fallback.")
            return response
        raise PreparationError("Fabric request retry bound exhausted.")

    def poll(self, url: str, *, success: str | set[str], pending: set[str], timeout: int,
             initial: Response | None = None, reserve: int = 0) -> dict:
        self.url(url)
        deadline = min(self.clock() + timeout, self.deadline - reserve)
        if initial is not None:
            self.wait(initial, deadline)
        successes = {success} if isinstance(success, str) else success
        while self.clock() < deadline:
            response = self.request("GET", url, deadline=deadline)
            body = _object(response.body, "Fabric operation")
            status = body.get("status")
            if not isinstance(status, str):
                raise PreparationError("Fabric operation returned no supported status.")
            if status in successes:
                return body
            if status in {"Failed", "Cancelled", "Canceled"}:
                raise TerminalFailure(status)
            if status not in pending:
                raise PreparationError(f"Fabric operation did not reach {success}; terminal/unknown status.")
            self.wait(response, deadline)
        raise DeadlineExceeded("Fabric operation deadline exhausted.")

    def operation_url(self, location: str) -> str:
        location = self.url(location)
        parsed = urllib.parse.urlsplit(location)
        if "?" in location or "#" in location or not re.fullmatch(r"/v1/operations/[A-Za-z0-9-]+", parsed.path):
            raise PreparationError("Fabric LRO Location is not an operation-state URL.")
        return location

    def _response_operation_url(self, location: str) -> str:
        parsed = urllib.parse.urlsplit(location)
        if parsed.netloc == "api.fabric.microsoft.com":
            return self.operation_url(location)
        if (parsed.scheme != "https"
                or not re.fullmatch(r"wabi-[a-z0-9-]+-redirect\.analysis\.windows\.net", parsed.netloc)
                or "?" in location or "#" in location
                or not re.fullmatch(r"/v1/operations/[0-9a-fA-F-]{36}", parsed.path)):
            raise PreparationError("Fabric LRO response Location origin/path is not trusted.")
        operation_id = _uuid(parsed.path.rsplit("/", 1)[1], "Returned Fabric operation ID")
        return self.operation_url(f"{API}/operations/{operation_id}")

    def job_resource(self, workspace: str, notebook: str, value: str) -> tuple[str, str]:
        value = self.url(value)
        parsed = urllib.parse.urlsplit(value)
        if ("?" in value or "#" in value or parsed.path.rsplit("/", 1)[0] !=
                f"/v1/workspaces/{workspace}/items/{notebook}/jobs/instances"):
            raise PreparationError("Notebook job Location is outside the sample item.")
        return value, _uuid(parsed.path.rsplit("/", 1)[1], "Returned notebook job ID")

    def operation(self, method: str, url: str, body: object = None,
                  accepted: Callable[[str], None] | None = None) -> dict:
        response = self.request(method, url, body)
        if response.status != 202:
            return _object(response.body, "Fabric response")
        location = response.header("Location")
        if not location:
            raise PreparationError("Fabric LRO accepted without Location; do not resubmit an ambiguous write.")
        location = self._response_operation_url(location)
        if accepted:
            accepted(location)
        self.poll(location, success="Succeeded", pending={"NotStarted", "Running"},
                  timeout=600, initial=response)
        return _object(self.request("GET", location.rstrip("/") + "/result").body, "Fabric LRO result")

    def items(self, workspace: str) -> list[dict]:
        return self.collection(workspace, "items")

    def collection(self, workspace: str, kind: str) -> list[dict]:
        if kind not in {"items", "roleAssignments"}:
            raise PreparationError("Unsupported Fabric inventory collection.")
        url = f"/workspaces/{workspace}/{kind}"
        items: list[dict] = []
        seen: set[str] = set()
        while url:
            full = self.url(url)
            if full in seen or len(seen) >= 100:
                raise PreparationError("Fabric pagination cycle/page bound exceeded.")
            seen.add(full)
            page = _object(self.request("GET", full).body, "Fabric item list")
            values = page.get("value")
            if not isinstance(values, list):
                raise PreparationError("Fabric inventory list is incomplete.")
            items.extend(_object(value, "Fabric item") for value in values)
            continuation = page.get("continuationUri", "")
            if not isinstance(continuation, str):
                raise PreparationError("Fabric pagination URI is invalid.")
            if page.get("continuationToken") and not continuation:
                raise PreparationError("Fabric continuation token has no URI.")
            if continuation and urllib.parse.urlsplit(continuation).path != f"/v1/workspaces/{workspace}/{kind}":
                raise PreparationError("Fabric pagination points outside the selected workspace collection.")
            url = continuation
        return items

    def workspace_writers(self, workspace: str) -> None:
        assignments = self.collection(workspace, "roleAssignments")
        operator = self.subject()
        if self.expected_subject and operator != self.expected_subject:
            raise PreparationError("Fabric operator changed during sample preparation.")
        self.expected_subject = operator
        found = False
        for assignment in assignments:
            role = assignment.get("role")
            if role == "Viewer":
                continue
            if role not in {"Admin", "Member", "Contributor"}:
                raise PreparationError("Sample workspace has unknown effective role evidence; unsupported.")
            principal = _object(assignment.get("principal"), "Workspace role principal")
            principal_id = principal.get("id")
            if not isinstance(principal_id, str):
                raise PreparationError("Workspace role principal has no supported UUID.")
            _uuid(principal_id, "Workspace role principal")
            if principal.get("type") not in {"User", "ServicePrincipal"} or principal_id.lower() != operator:
                raise PreparationError("Sample workspace has another/group writer; use an exclusively writable development workspace.")
            found = True
        if not found:
            raise PreparationError("Sample workspace writer inventory does not prove the submitting operator.")

    def notebook_writers(self, workspace: str, notebook: str) -> None:
        self.workspace_writers(workspace)
        evidence = _object(self.request(
            "GET", f"/admin/workspaces/{workspace}/items/{notebook}/users",
        ).body, "Notebook effective access inventory")
        if evidence.get("continuationToken") or evidence.get("continuationUri"):
            raise PreparationError("Notebook effective access inventory is incomplete/unsupported.")
        details = evidence.get("accessDetails")
        if not isinstance(details, list):
            raise PreparationError("Notebook effective access inventory is unsupported.")
        owner_write = False
        for entry in details:
            entry = _object(entry, "Notebook access entry")
            principal = _object(entry.get("principal"), "Notebook access principal")
            principal_id = principal.get("id")
            if not isinstance(principal_id, str):
                raise PreparationError("Notebook access principal has no supported UUID.")
            _uuid(principal_id, "Notebook access principal")
            access = _object(entry.get("itemAccessDetails"), "Notebook access permissions")
            permissions, additional = access.get("permissions"), access.get("additionalPermissions")
            if (access.get("type") != "Notebook" or not isinstance(permissions, list)
                    or not isinstance(additional, list) or any(not isinstance(value, str)
                    for value in [*permissions, *additional])):
                raise PreparationError("Notebook effective permission evidence is malformed/unsupported.")
            is_operator = (principal.get("type") in {"User", "ServicePrincipal"}
                           and principal_id.lower() == self.expected_subject)
            permissions = ["Reshare" if permission == "ReShare" else permission for permission in permissions]
            if any(permission not in {"Read", "Write", "Reshare", "Explore", "Execute"} for permission in permissions):
                raise PreparationError("Notebook has an unknown effective permission; unsupported.")
            if any(permission not in {"ReadAll", "viewOutput"} for permission in additional):
                raise PreparationError("Notebook has unknown workload access; unsupported.")
            if not is_operator and set(permissions) - {"Read"}:
                raise PreparationError("Notebook has another/group effective writer or delegator; unsupported.")
            if is_operator and "Write" in permissions:
                owner_write = True
        if not owner_write:
            raise PreparationError("Notebook effective access does not prove exclusive operator write; unsupported.")


class StateStore(Protocol):
    def load(self) -> dict: ...
    def save(self, value: dict) -> None: ...


class FileState:
    def __init__(self, path: Path):
        self.path = path

    def trusted_path(self) -> None:
        for path in (self.path, self.path.with_suffix(".lock"), *self.path.parents):
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            except OSError:
                raise PreparationError("Sample state path cannot be inspected.") from None
            if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 1024:
                raise PreparationError("Sample state path contains a link/reparse point; use a private trusted directory.")
            if os.name != "nt" and path in {self.path, self.path.with_suffix(".lock"), self.path.parent}:
                if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
                    raise PreparationError("Sample state must be operator-owned and not writable by group/others.")

    def load(self) -> dict:
        self.trusted_path()
        if not self.path.exists():
            return {}
        try:
            return _object(json.loads(self.path.read_text(encoding="utf-8")), "Sample state")
        except (OSError, ValueError):
            raise PreparationError("Sample state cannot be read; do not adopt existing resources.") from None

    def save(self, value: dict) -> None:
        temp: Path | None = None
        failure: PreparationError | None = None
        try:
            self.trusted_path()
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.trusted_path()
            descriptor, name = tempfile.mkstemp(prefix="fabric-test-state-", suffix=".tmp", dir=self.path.parent)
            temp = Path(name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, indent=2)
            self.trusted_path()
            temp.replace(self.path)
        except PreparationError as error:
            failure = error
        except OSError:
            failure = PreparationError("Sample state cannot be persisted; stop and reconcile actual operations.")
        finally:
            if temp is not None:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    prefix = f"{failure} " if failure else ""
                    failure = PreparationError(prefix + "Owned temporary state cleanup failed; reconcile before retry.")
        if failure:
            raise failure from None

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.trusted_path()
        lock = self.path.with_suffix(".lock")
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.trusted_path()
            handle = lock.open("x")
        except FileExistsError:
            raise PreparationError("Sample preparation is locked; resolve an interrupted/concurrent run before retrying.") from None
        except OSError:
            raise PreparationError("Sample state lock cannot be created in the trusted operator directory.") from None
        try:
            with handle:
                handle.write(str(os.getpid()))
                yield
        finally:
            lock.unlink()


def _definition(notebook: dict) -> dict:
    return {"format": "ipynb", "parts": [{
        "path": "notebook-content.ipynb",
        "payload": base64.b64encode(json.dumps(notebook).encode()).decode(),
        "payloadType": "InlineBase64",
    }]}


def _notebook_contract(definition: dict) -> dict:
    parts = definition.get("parts", [])
    selected = [part for part in parts if part.get("path") == "notebook-content.ipynb"]
    if len(selected) != 1 or len(parts) not in {1, 2}:
        raise PreparationError("Sample notebook definition has unexpected parts.")
    try:
        notebook = _object(json.loads(base64.b64decode(selected[0]["payload"], validate=True)), "Notebook")
        cells = [{"cell_type": cell["cell_type"], "source": "".join(cell["source"]),
                  "tags": cell.get("metadata", {}).get("tags", [])} for cell in notebook["cells"]]
        return {"dependencies": notebook["metadata"]["dependencies"], "cells": cells}
    except (KeyError, ValueError, TypeError):
        raise PreparationError("Sample notebook definition is malformed.") from None


def _ontology_contract(definition: dict) -> dict:
    try:
        parts = {part["path"]: json.loads(base64.b64decode(part["payload"], validate=True))
                 for part in definition["parts"] if part["path"] != ".platform"}
    except (KeyError, ValueError, TypeError):
        raise PreparationError("Sample ontology definition is malformed.") from None
    if len(parts) != len(definition["parts"]) - 1:
        raise PreparationError("Sample ontology definition has duplicate/missing parts.")
    binding_id = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    for path, payload in parts.items():
        if not isinstance(path, str):
            raise PreparationError("Sample ontology definition part path must be a string.")
        value = _object(payload, "Ontology definition part")
        table = None
        if re.fullmatch(rf"EntityTypes/[1-9][0-9]*/DataBindings/{binding_id}\.json", path):
            configuration = _object(value.get("dataBindingConfiguration"), "Ontology data binding")
            if configuration.get("dataBindingType") == "NonTimeSeries":
                table = _object(configuration.get("sourceTableProperties"), "Ontology source table")
        elif re.fullmatch(rf"RelationshipTypes/[1-9][0-9]*/Contextualizations/{binding_id}\.json", path):
            table = _object(value.get("dataBindingTable"), "Ontology contextualization table")
        # Fabric readback adds null for an omitted optional Lakehouse schema.
        if table is not None and table.get("sourceType") == "LakehouseTable" and table.get("sourceSchema") is None:
            table.pop("sourceSchema", None)
    return parts


def prepare(settings: Configuration, api: FabricClient, store: StateStore) -> dict[str, str] | None:
    if not settings.deploy_test:
        return None
    data = generate_data(small=True)
    digest = _digest(data)
    prefix, scope = sample_identity(settings)
    owner = f"enterprise-iq-test:{VERSION}:{scope}:{digest}"
    state = store.load()
    identity = {"workspace": settings.workspace, "environment": settings.environment,
                "version": VERSION, "digest": digest}
    if state and any(state.get(key) != value for key, value in identity.items()):
        raise PreparationError("Sample state ownership/version does not match this deployment.")
    state = state or {**identity, "items": {}, "status": "preparing"}
    completed = state.get("verified") is True
    state["status"] = "preparing"
    store.save(state)
    base = f"/workspaces/{settings.workspace}"
    try:
        workspace = _object(api.request("GET", base).body, "Fabric workspace")
        if workspace.get("id") != settings.workspace or not workspace.get("capacityId"):
            raise PreparationError("Explicit sample workspace needs an existing supported Fabric capacity.")
        api.workspace_writers(settings.workspace)
        inventory = api.items(settings.workspace)

        def verify(item: dict, kind: str, name: str) -> str:
            item_id = item.get("id", "")
            if not isinstance(item_id, str):
                raise PreparationError("Sample item response has no UUID.")
            _uuid(item_id, "Sample returned item ID")
            if (item.get("type") != kind or item.get("workspaceId") != settings.workspace
                    or item.get("displayName") != name or item.get("description") != owner):
                raise PreparationError(f"Sample {kind} ownership/type/name mismatch; never adopt or overwrite.")
            return item_id

        def ensure(kind: str, plural: str, definition: dict | None = None) -> str:
            name = f"{prefix}_{kind}"
            records = _object(state["items"], "Sample item records")
            record = records.get(kind)
            matches = [item for item in inventory if item.get("displayName") == name]
            if kind == "Lakehouse" and record is not None and len(matches) > 1:
                recorded = _object(record, "Sample lakehouse record").get("id")
                if not isinstance(recorded, str):
                    raise PreparationError("Recorded sample lakehouse has no supported UUID.")
                recorded = _uuid(recorded, "Recorded sample lakehouse ID")
                parent = _object(api.request("GET", f"{base}/lakehouses/{recorded}").body, "Owned lakehouse")
                if verify(parent, kind, name) != recorded:
                    raise PreparationError("Sample Lakehouse recorded ownership changed.")
                sql = _object(_object(parent.get("properties"), "Lakehouse properties").get(
                    "sqlEndpointProperties"), "Lakehouse SQL endpoint")
                sql_id = sql.get("id")
                if not isinstance(sql_id, str):
                    raise PreparationError("Owned lakehouse SQL endpoint has no supported UUID.")
                sql_id = _uuid(sql_id, "Owned lakehouse SQL endpoint ID")
                matches = [item for item in matches if not (
                    item.get("type") == "SQLEndpoint" and item.get("id") == sql_id
                    and item.get("workspaceId") == settings.workspace
                )]
            if len(matches) > 1:
                raise PreparationError(f"Sample {kind} collision: multiple matching items.")
            if record is not None:
                record = _object(record, "Sample item record")
                item_id = record.get("id")
                if not isinstance(item_id, str):
                    raise PreparationError("Sample item state has no UUID.")
                _uuid(item_id, "Recorded sample item ID")
                item = _object(api.request("GET", f"{base}/items/{item_id}").body, "Sample item")
                if verify(item, kind, name) != item_id or len(matches) != 1 or matches[0].get("id") != item_id:
                    raise PreparationError("Sample inventory/recorded ownership changed.")
            else:
                pending = state.get("pending")
                if pending:
                    pending = _object(pending, "Sample pending operation")
                    if pending.get("kind") != kind or not pending.get("operation"):
                        raise PreparationError("Ambiguous sample create acceptance; resolve saved intent, do not adopt collision.")
                    operation = pending["operation"]
                    if not isinstance(operation, str):
                        raise PreparationError("Invalid saved operation URL.")
                    operation = api.operation_url(operation)
                    api.poll(operation, success="Succeeded", pending={"Running", "NotStarted"}, timeout=600)
                    item = _object(api.request("GET", operation.rstrip("/") + "/result").body, "Sample create result")
                else:
                    if matches:
                        raise PreparationError(f"Sample {kind} collision: ownership cannot be proved from a name.")
                    if completed:
                        raise PreparationError(f"Completed sample {kind} record missing; no recreation.")
                    state["pending"] = {"kind": kind}
                    store.save(state)
                    body = {"displayName": name, "description": owner}
                    if definition is not None:
                        body["definition"] = definition

                    def accepted(operation: str) -> None:
                        state["pending"] = {"kind": kind, "operation": operation}
                        store.save(state)

                    try:
                        item = api.operation("POST", f"{base}/{plural}", body, accepted=accepted)
                    except RequestRejected:
                        state.pop("pending", None)
                        store.save(state)
                        raise
                item_id = verify(item, kind, name)
                records[kind] = {"id": item_id}
                state.pop("pending", None)
                store.save(state)
                inventory.append(item)
            if definition is not None:
                if kind == "Ontology":
                    ontology = _object(api.request("GET", f"{base}/ontologies/{item_id}").body, "Sample ontology")
                    verify(ontology, kind, name)
                    if _object(ontology.get("properties"), "Sample ontology properties").get("generation") != 1:
                        raise PreparationError("Sample ontology generation is not the explicit JSON generation 1.")
                actual = api.operation("POST", f"{base}/{plural}/{item_id}/getDefinition"
                                       + ("?format=ipynb" if kind == "Notebook" else ""))
                actual_definition = _object(actual.get("definition"), "Sample definition")
                contract = _notebook_contract if kind == "Notebook" else _ontology_contract
                if contract(actual_definition) != contract(definition):
                    raise PreparationError(f"Sample {kind} definition/binding drift; no replacement.")
            return item_id

        lakehouse_id = ensure("Lakehouse", "lakehouses")
        lakehouse_name = prefix + "_Lakehouse"
        notebook = build_sample_notebook(settings.workspace, lakehouse_id, lakehouse_name, data)
        notebook_id = ensure("Notebook", "notebooks", _definition(notebook))
        api.notebook_writers(settings.workspace, notebook_id)
        verify(_object(api.request("GET", f"{base}/items/{lakehouse_id}").body, "Sample lakehouse"),
               "Lakehouse", lakehouse_name)
        current = api.operation("POST", f"{base}/notebooks/{notebook_id}/getDefinition?format=ipynb")
        if _notebook_contract(_object(current.get("definition"), "Notebook pre-execution definition")) != _notebook_contract(_definition(notebook)):
            raise PreparationError("Notebook definition drift immediately before execution; no submission.")
        job_url = state.get("job")
        if job_url is None:
            if state.get("job_intent"):
                raise PreparationError("Ambiguous notebook submission; resolve saved job intent before retry.")
            if api.clock() + 150 >= api.deadline:
                raise DeadlineExceeded("Insufficient preparation budget before notebook submission; retry explicitly.")
            state["job_intent"] = True
            store.save(state)
            try:
                response = api.request("POST", f"{base}/notebooks/{notebook_id}/jobs/execute/instances?beta=false", {
                    "parameters": [{"name": "allow_create", "value": not completed, "type": "Boolean"}],
                    "executionData": {
                        "compute": "Spark",
                        "computeConfiguration": {"defaultLakehouse": {
                            "referenceType": "ById", "itemId": lakehouse_id, "workspaceId": settings.workspace,
                        }},
                    },
                }, deadline=api.deadline - 120)
            except RequestRejected:
                state.pop("job_intent", None)
                store.save(state)
                raise
            job_url = response.header("Location")
            if response.status != 202 or not job_url:
                raise PreparationError("Notebook submission has no accepted job Location; do not resubmit.")
            job_url, job_id = api.job_resource(settings.workspace, notebook_id, job_url)
            state["job"] = job_url
            state.pop("job_intent", None)
            store.save(state)
            initial = response
        else:
            initial = None
        if not isinstance(job_url, str):
            raise PreparationError("Recorded notebook job URL is invalid.")
        job_url, job_id = api.job_resource(settings.workspace, notebook_id, job_url)
        try:
            result = api.poll(job_url, success="Completed", pending={"NotStarted", "InProgress", "Running"},
                              timeout=1800, initial=initial, reserve=120)
        except DeadlineExceeded:
            state["cancellation"] = {"job": job_url, "status": "requested"}
            store.save(state)
            try:
                cancellation = api.request("POST", job_url + "/cancel")
                if cancellation.status != 202 or api.url(cancellation.header("Location")) != job_url:
                    raise PreparationError("Notebook cancellation has no matching accepted Location.")
                canceled = api.poll(job_url, success={"Cancelled", "Canceled"},
                                    pending={"NotStarted", "InProgress", "Running"},
                                    timeout=120, initial=cancellation)
            except PreparationError:
                state["cancellation"]["status"] = "unconfirmed"
                store.save(state)
                raise PreparationError(
                    "Notebook deadline exhausted; cancellation unconfirmed. Recorded job may still consume capacity; reconcile it."
                ) from None
            state["cancellation"]["status"] = "confirmed"
            state["last_failed_job"] = {"url": job_url, "status": canceled["status"]}
            state.pop("job", None)
            store.save(state)
            raise PreparationError("Notebook deadline exhausted; owned job cancellation confirmed. Retry explicitly.") from None
        except TerminalFailure as error:
            if error.status in {"Failed", "Cancelled", "Canceled"}:
                state["last_failed_job"] = {"url": job_url, "status": error.status}
                state.pop("job", None)
                store.save(state)
            raise
        if result.get("id") != job_id or result.get("itemId") != notebook_id or result.get("failureReason"):
            raise PreparationError("Notebook job result identity/failure does not match the sample run.")
        verification = _object(api.request(
            "GET", f"{base}/notebooks/{notebook_id}/jobs/execute/instances/{job_id}?beta=true",
        ).body, "Notebook verification result")
        exit_value = verification.get("exitValue")
        if "properties" in verification:
            properties = _object(verification["properties"], "Notebook verification properties")
            exit_value = properties.get("exitValue")
            if (properties.get("compute") != "Spark" or
                    "exitValue" in verification and verification["exitValue"] != exit_value):
                raise PreparationError("Notebook verification has inconsistent compute or exit value evidence.")
        if (verification.get("id") != job_id or verification.get("itemId") != notebook_id
                or verification.get("status") != "Completed" or verification.get("failureReason")
                or exit_value != _verification_value(settings.workspace, lakehouse_id, digest)):
            raise PreparationError("Notebook completed without the exact twelve-table verification exit value.")
        state.pop("job", None)
        state["verified"] = True
        store.save(state)
        ontology_name = prefix + "_Ontology"
        ontology_id = ensure("Ontology", "ontologies", {"parts": build_ontology_parts(
            settings.workspace, lakehouse_id, ontology_name,
        )})
        deadline = min(api.clock() + 600, api.deadline)
        while True:
            response = api.request("GET", f"{base}/lakehouses/{lakehouse_id}")
            lakehouse = _object(response.body, "Sample lakehouse properties")
            verify(lakehouse, "Lakehouse", lakehouse_name)
            properties = _object(lakehouse.get("properties"), "Sample lakehouse properties")
            sql = _object(properties.get("sqlEndpointProperties"), "Sample SQL endpoint")
            if sql.get("provisioningStatus") == "Success":
                host = sql.get("connectionString")
                if not isinstance(host, str):
                    raise PreparationError("Sample SQL endpoint has no connectionString.")
                _hostname(host, "Returned sample SQL endpoint")
                break
            if sql.get("provisioningStatus") not in {"NotStarted", "InProgress"}:
                raise PreparationError("Sample SQL endpoint provisioning failed or returned an unknown status.")
            api.wait(response, deadline)
        api.workspace_writers(settings.workspace)
        outputs = {
            "FABRIC_TEST_WORKSPACE_ID": settings.workspace,
            "FABRIC_TEST_LAKEHOUSE_ID": lakehouse_id,
            "FABRIC_TEST_ONTOLOGY_ID": ontology_id,
            "FABRIC_TEST_SQL_ENDPOINT": host,
            "FABRIC_TEST_SQL_DATABASE": lakehouse_name,
            "FABRIC_TEST_ONTOLOGY_MCP_URL": (
                f"{API}/mcp/dataPlane/workspaces/{settings.workspace}/items/{ontology_id}/ontologyEndpoint"
            ),
            "FABRIC_TEST_SCHEMA_VERSION": VERSION,
            "FABRIC_TEST_PROVISIONING_STATUS": "ready",
        }
        state["status"] = "ready"
        state["outputs"] = outputs
        store.save(state)
        return outputs
    except PreparationError:
        state["status"] = "failed"
        store.save(state)
        raise


def validate_ready(settings: Configuration, values: Mapping[str, str], state: dict) -> dict[str, str]:
    if not settings.deploy_test:
        return settings.existing
    if (state.get("status") != "ready" or state.get("version") != VERSION
            or state.get("workspace") != settings.workspace or state.get("environment") != settings.environment):
        raise PreparationError("Sample configuration is not ready. Run explicitly opted-in azd up, not image-only deploy.")
    outputs = _object(state.get("outputs"), "Completed sample outputs")
    expected = {f"FABRIC_TEST_{key}" for key in KEYS} | {
        "FABRIC_TEST_SCHEMA_VERSION", "FABRIC_TEST_PROVISIONING_STATUS",
    }
    if (set(outputs) != expected or outputs.get("FABRIC_TEST_SCHEMA_VERSION") != VERSION
            or outputs.get("FABRIC_TEST_PROVISIONING_STATUS") != "ready"):
        raise PreparationError("Completed sample handoff status/version/fields are incomplete.")
    if any(values.get(key) != value for key, value in outputs.items()):
        raise PreparationError("Generated sample settings differ from completed state; run azd up.")
    required = [f"FABRIC_TEST_{key}" for key in KEYS]
    if any(not isinstance(outputs.get(key), str) or not outputs[key] for key in required):
        raise PreparationError("Completed sample references are incomplete.")
    return {f"FABRIC_{key}": outputs[f"FABRIC_TEST_{key}"] for key in KEYS}


def validate_deployed(settings: Configuration, values: Mapping[str, str], selected: dict[str, str]) -> None:
    mode = "test-sample" if settings.deploy_test else (
        "existing" if selected["FABRIC_SQL_ENDPOINT"] or selected["FABRIC_ONTOLOGY_MCP_URL"]
        or selected["FABRIC_ONTOLOGY_ID"] else "unconfigured"
    )
    active_keys = [f"FABRIC_ACTIVE_{key}" for key in KEYS] + ["FABRIC_ACTIVE_DATA_MODE"]
    if not settings.deploy_test and not any(key in values for key in active_keys):
        return
    if values.get("FABRIC_ACTIVE_DATA_MODE") != mode or any(
        values.get(f"FABRIC_ACTIVE_{key}") != selected[f"FABRIC_{key}"] for key in KEYS
    ):
        raise PreparationError("Fabric selection changed since provisioning; run azd up or provision the prepared configuration.")


def cli_token() -> str:
    executable = "az.cmd" if os.name == "nt" else "az"
    try:
        result = subprocess.run([executable, "account", "get-access-token", "--resource",
                                 "https://api.fabric.microsoft.com", "--query", "accessToken", "-o", "tsv"],
                                capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        raise PreparationError("Cannot obtain Fabric operator token from Azure CLI.") from None
    if result.returncode:
        raise PreparationError("Fabric operator authentication failed; authenticate to the intended tenant.")
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("Prepare", "Validate", "ValidateDeployed"), required=True)
    args = parser.parse_args()
    settings = Configuration.from_env(os.environ)
    if not settings.deploy_test:
        if args.mode == "ValidateDeployed":
            validate_deployed(settings, os.environ, settings.existing)
        print(json.dumps({"status": "existing" if any(settings.existing[f"FABRIC_{key}"] for key in (
            "SQL_ENDPOINT", "ONTOLOGY_ID", "ONTOLOGY_MCP_URL")) else "unconfigured", "outputs": {}}))
        return
    store = FileState(ROOT / ".azure" / settings.environment / "fabric-test-state.json")
    if args.mode == "Prepare":
        with store.locked():
            outputs = prepare(settings, FabricClient(cli_token), store)
        print(json.dumps({"status": "ready", "outputs": outputs}))
    else:
        selected = validate_ready(settings, os.environ, store.load())
        if args.mode == "ValidateDeployed":
            validate_deployed(settings, os.environ, selected)
        print(json.dumps({"status": "ready", "outputs": {}}))


if __name__ == "__main__":
    try:
        main()
    except PreparationError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
