# Azure infrastructure

See [RUNBOOK.md](../RUNBOOK.md) for explicit deployment steps. `main.bicep`
deploys a resource group, separate UI/MCP identities, ACR, Container Apps/log environment, internal MCP
and Entra Easy Auth-protected Next UI.

Set generic Fabric workspace/lakehouse/ontology/SQL IDs, Entra client/tenant and
secure client secret parameters. Copilot BYOK endpoint/model/provider/wire API
are MCP-only env vars; key/bearer parameters are `@secure()` and become
Container Apps secret references. M6's Foundry endpoint/deployments and M7 agent
remain separate. No templates create paid model deployments or Fabric datasets;
an explicitly enabled `preup` hook can prepare dedicated synthetic Fabric items.

Hosted defaults use `IQ_DEMO_MODE=0`: no demonstration connections are created.
For an explicitly approved synthetic-fixture test only, set `IQ_DEMO_MODE=1`
in azd (propagated to both services). Use `MCP_CPU=1.0` (paired with 2 GiB) and approved Azure model
endpoint/deployment values. `COPILOT_USE_AZURE_IDENTITY=1` avoids static provider
credentials for key-disabled model accounts; separately grant the
`MCP_MANAGED_IDENTITY_PRINCIPAL_ID` output the scoped model invocation role.
Only the backend identity receives that role, never the UI identity.
Fabric parameters may also be intentionally empty with demo mode off; real SDK
chat then remains ungrounded. Configured SQL and/or ontology requires actual
explicit references and permissions, not merely an enabled Fabric tenant.
Synthetic tools still use real stdio MCP processes resolved from the installed
Python package, not an assumed source-tree location.

Set `COPILOT_AZURE_RESOURCE_ID` to the approved model account ARM resource ID
for enabled-model discovery. Hosted inventory always uses the MCP managed
identity, including when model invocation uses a static key. No fallback to
developer credentials or a global catalog occurs in Azure. Set nonsecret
`IQ_MCP_TRUST_POLICY` and `IQ_MCP_AUTH_REFERENCES` JSON through protected
operator configuration for real remote connections (see [MCP.md](../docs/MCP.md)).
These azd settings are wired through the Bicep parameters; header values remain
existing Container Apps secret references, not JSON. Templates do not create
new model grants or arbitrary authentication secrets.

Set a cryptographically random `MCP_SERVICE_TOKEN` of at least 32 characters
through a protected azd environment/secret manager, never a committed literal.
It authenticates internal HTTP calls independently of delegated Fabric tokens.
Both apps are capped at one replica. MCP has HTTP health probes; UI probes use
TCP so they do not depend on sign-in. Easy Auth protects all external paths
and redirects anonymous callers; the application also denies anonymous APIs.
The Easy Auth token store is disabled because no backing Blob SAS/storage is
provisioned. Public runtime Entra IDs remain available through `/api/me`.

## Optional Fabric test ontology

`FABRIC_DEPLOY_TEST_ONTOLOGY` accepts only `0` or `1`, defaults to `0`, and is
independent of `IQ_DEMO_MODE`. Default/0 keeps the original six integration
settings unchanged and performs no Fabric API/token/generation/sample-state
operations. SQL-only, ontology-only and all-empty integrations remain supported.
Only malformed/partial supplied item references fail local configuration
preflight; configuration presence is not a permission/health check.

For an authorized sample deployment, set `FABRIC_TEST_WORKSPACE_ID` to an
already approved development workspace UUID and the flag to `1`, then use
`azd up` only with the complete writer-inventory authorization below already
approved. Do not grant tenant-wide Fabric Admin/`Tenant.Read.All` just for this
flag. Otherwise use flag `0` with existing references, or separately prepare
clearly labelled samples through your approved process and reuse their explicit
references; manually supplied data's origin is not inferred by deployment.
Python 3.11+ and the already required PowerShell 7/Azure CLI/azd must
be available to the deployment operator. No additional Python package is needed
for local configuration validation (all modes) or sample preparation. Flag 1
rejects `IQ_DEMO_MODE=1` rather than provisioning real samples for demo fixtures.
The preup hook first runs the unchanged auth-input
validator, then generates a small deterministic synthetic dataset and prepares
only three dedicated versioned sample items: Lakehouse, Notebook and explicit
JSON-generation-1 Ontology. It waits for verified notebook completion and actual
item/SQL endpoint values; it does not call the older overwrite/upload helpers.

The workspace needs supported existing capacity and enabled Fabric/ontology
preview tenant settings. The provisioning operator needs Contributor-or-higher
plus reviewed item read/write/metadata and notebook execution permissions.
Runtime SQL read-only access and delegated ontology entitlement are independent.
Ontology MCP currently documents a service-principal limitation; supported item
creation does not prove indexing or user search access. No capacity, workspace,
consent, tenant settings or grants are provisioned.
The [notebook-specific release execution API](https://learn.microsoft.com/en-us/rest/api/fabric/notebook/background-jobs/run-on-demand-notebook)
(`POST .../notebooks/<id>/jobs/execute/instances?beta=false`) and notebook-specific
`beta=true` verification-result endpoint must be available. Execution specifies
Spark, the exact owned default Lakehouse by ID/workspace, and the typed Boolean
`allow_create` notebook parameter. The generic item-job parameter surface is
not used: acceptance alone does not prove notebook parameter injection.
The tagged parameter cell still defaults to `False`; initial preparation sends
`True`, and previously verified samples send `False` so missing tables fail
instead of being recreated. No persisted notebook definition is changed for this
request-contract fix. Existing tables must match schema, count and exact content;
only absent tables may be created with `errorifexists`, never overwritten.
Unsupported parameters,
missing exact `exitValue` or result access failures block handoff.
Verification reads the published Spark result's `properties.exitValue`, retaining
the legacy top-level shape only when no `properties` object is present. Malformed,
missing or conflicting evidence is rejected; a Completed job alone is not ready.
Sample mode additionally requires an exclusively writable development workspace,
existing Member-or-higher role-inventory access (`Workspace.Read.All` or its
read/write equivalent), and authorized Fabric Admin item-access inventory
(`Tenant.Read.All` or equivalent; existing administrator/approved service
principal). All workspace-role pages and effective notebook permissions must
prove the submitting operator is the only writer/delegator; other/group writers,
unknown or incomplete evidence and unsupported APIs fail closed. The hook
rechecks definition/resource integrity just before running the owned job.
The observed `ReShare` spelling is treated as the documented `Reshare`
delegation permission, never as read-only access. On resume, a same-name
automatic SQL endpoint is excluded from Lakehouse collision checks only when
its exact ID is linked by the recorded, ownership-verified Lakehouse metadata.
Unrelated same-name items still block preparation.
Generation-1 ontology parts emit fixed Microsoft schema annotations and empty
untyped-property collections. Readback comparison treats only an omitted versus
`null` optional `sourceSchema` as equivalent at the exact generated NonTimeSeries
Lakehouse binding and relationship contextualization locations. The published
schema declares this optional property as a string, so no `null` schema is sent
on write. Nonnull schema differences, unknown fields, item IDs and property/key
mappings remain strict drift failures; no ontology definition is replaced.
It grants nothing, and default/0 adds no Admin/inventory/token/API requirement.
Privileged tenant/capacity administrators and concurrent operator activity
remain trusted control-plane boundaries, not an immutable revision guarantee.

Generated `FABRIC_TEST_LAKEHOUSE_ID`, `FABRIC_TEST_ONTOLOGY_ID`,
`FABRIC_TEST_SQL_ENDPOINT`, `FABRIC_TEST_SQL_DATABASE`,
`FABRIC_TEST_ONTOLOGY_MCP_URL`, `FABRIC_TEST_SCHEMA_VERSION` and
`FABRIC_TEST_PROVISIONING_STATUS` are nonsecret outputs, not user-selected write
targets. Ready is recorded only after all steps succeed and settings persist.
State under ignored `.azure\<environment>` records ownership/version digests,
actual IDs and operation/job references, never tokens or payload rows. Preserve
it for retries; never delete it to adopt a colliding resource.
Regional workload LRO response locations with a strict operation UUID are
mapped to the official `https://api.fabric.microsoft.com/v1/operations/<UUID>`
state/result routes. Tokens never go to the workload host; saved operation
URLs, resource URLs and notebook job URLs retain their strict official-origin
guards. Operation success alone does not replace item/content verification.
Keep that state directory private to the authorized operator. Link/reparse-point
paths are rejected and updates use exclusive unique temporary files and atomic
replacement; no ACLs are provisioned or modified.

Bicep selects those completed sample references only at flag1, retaining the
original `FABRIC_*` azd inputs. Existing runtime variable names are unchanged;
`FABRIC_DATA_MODE=test-sample` and `FABRIC_ACTIVE_*` outputs disclose the selected
synthetic integration. SDK answer guidance labels sample-derived results, not
production/customer records or fictitious evidence. This does not enable demo
registry connections or change model/auth configuration.

Preprovision preserves the original auth hook **first**, followed by read-only
sample/configuration validation. Preview and standalone provision never prepare
Fabric items. Predeploy is also read-only and rejects changed selected mode/refs
since the last successful provision. A new sample selection needs `azd up`;
image-only deployment cannot apply changed resource configuration.
Legacy flag-0 environments with no active-selection outputs retain image-only
deployment after local validation; prior Fabric runtime selection stays unknown
until reprovision. Partial active outputs are rejected.

Repeated runs verify ownership and content; existing sample tables/definitions
are not overwritten or appended to. Unknown collisions, drift, uncertain
create/job acceptance, terminal failure or deadlines stop explicitly, with no
automatic deletion. To return to your original integration, set the flag to0
and reprovision with exact current auth keys unchanged; sample items remain.
See [the runbook](../RUNBOOK.md#guarded-deployment-sample-explicit-opt-in) for
recovery boundaries, including interrupted operations and stale local locks.
Notebook timeout requests and tracks cancellation of only the recorded owned
job, with confirmed or explicit unknown outcome. Accepted cancellation does
not prove compute stopped or prevent capacity charges; unresolved jobs retain
their IDs and block unsafe resubmission.

Credential-free checks (no deployment, tenant reads or inference):

```powershell
.\.venv\Scripts\python -m pytest tests\test_fabric_provisioning.py tests\test_copilot.py tests\test_generic_snapshot.py tests\test_agent_http.py -q
.\infra\Test-FabricPreparation.ps1
.\infra\Test-AuthReferences.ps1
```

The hook tests use CLI/azd shims and inactive deterministic auth fixtures.
The existing auth check compiles Bicep locally. These checks prove workflow
logic, not live capacity, permissions, ontology indexing or delegated search.

## Preserve custom Easy Auth authority

Every provision requires `WEBUI_AUTH_SIGNING_SECRET_NAME` and
`WEBUI_AUTH_ENCRYPTION_SECRET_NAME` in the deployment environment. Use the exact
active UI-app secret names: distinct lowercase names of at most 20 characters.
The template always binds these names through `authConfigs/current`'s
`encryptionSettings` and retains both entries in the UI app's secrets array.
There is no empty-name/generated-key fallback.

`WEBUI_AUTH_SIGNING_KEY` and `WEBUI_AUTH_ENCRYPTION_KEY` are required protected
deployment-process inputs on **every provision**. Name-only updates were rejected
by the platform; omission is not a supported preservation mechanism. For an
existing rotated app, an authorized operator/deployment process must privately
retrieve the exact active values from the app's secret store (the supported
`listSecrets` operation) or the approved secret manager, and resupply them
unchanged in process memory. Never print the response, copy values into local
files/azd `.env`, pass literals in command arguments, or share them with an agent
or browser. Verify both names match the active auth configuration and supplied
values match their active secrets before provisioning. If the inputs cannot be
obtained safely, stop: do not provision, auto-generate replacements, substitute
old keys, or reset `encryptionSettings` to `{}`. Image-only deploy does not require
reprovisioning this template. Do not replay an older template lacking these refs.

Both value inputs are required 44-character `@secure()` strings at both Bicep
scopes, never outputs or container environment variables. Both must be canonical
Base64 encodings of exactly 32 bytes, independently generated for signing and
encryption. Actual authConfig validation rejected hexadecimal serialization;
64 hexadecimal characters decode as 48 bytes when interpreted as Base64, not
the required 32 bytes. Do not use hexadecimal or Base64url encoding.
The `azd` preprovision hook validates canonical encoding, decoded byte length and
distinctness without printing values. PowerShell 7+ must be available to run it.
Before any direct ARM/Bicep provision outside azd, run
`.\infra\Assert-AuthKeyInputs.ps1` in the protected deployment process with those
same process-environment inputs; Bicep length constraints alone do not validate
Base64 semantics. Only a separately
approved **new app** or explicit rotation should generate independent keys in
the supported encoding (for example, standard Base64 serialization of each
independent cryptographically random 32-byte array, kept entirely in memory).
Routine reprovision must preserve the exact active strings, not reserialize or
replace them. Missing/empty inputs fail
validation instead of falling back to generated authority.
This template does not create a Key Vault or grant vault access. Existing
Key Vault-backed secrets also require their URL/identity metadata to be retained
in reviewed infrastructure; do not convert them to name-only direct secrets.

Run `.\infra\Test-AuthReferences.ps1` from the repository root in PowerShell 7+
with Azure CLI/Bicep installed for credential-free
Bicep compilation, positive Base64 and negative hexadecimal/malformed/decoded-size/
shared-key regressions, and reference/secure-input preservation checks. It uses
only inactive deterministic fixtures and restores the process environment. This does
not deploy resources or prove live session invalidation.

For approved rotation and old/new-session verification, see
[the authentication runbook](../RUNBOOK.md#easy-auth-authority-rotation).

The operator must approve the exact subscription, region, recurring costs, new
Entra application and role grants before provisioning. Create a dedicated
single-tenant app with the actual
`https://<webui-fqdn>/.auth/login/aad/callback` web redirect. Configure its
ID-token hybrid flow as required by Easy Auth, and set
`api.requestedAccessTokenVersion=2` for bearer API tokens to match the configured
v2 issuer. Easy Auth's redirect provider name is case-sensitive
(`azureActiveDirectory`). The token client application is restricted to the
dedicated registration; do not emit an empty application allowlist, which denies
legitimate application tokens. A synthetic-only test does not require Fabric
delegated scopes or a browser SPA redirect.

The Python Docker image pre-provisions the pinned Copilot SDK runtime, ODBC 18,
shared Fabric helpers and generic schema. The Next image bundles the PDF worker.
Keep the app behind Easy Auth and the MCP ingress private. UI uses one replica
while document uploads use process-local ephemeral storage.

The registry JSON and document catalog must be operator-owned mounted files or
reviewed additions to a custom image; credentials remain environment/secret
references. For production, rotate credentials through Key Vault-backed secrets.
Do not bake `.env` files into either image.
