# Accelerator runbook

## 1. Credential-free checks

Run the README's unit/integration tests and build. Enable both server and UI
demo flags for local MCP/document smoke tests. Bind local services to loopback.
`IQ_LOCAL_AUTH_BYPASS=1` is for non-production local development only; production
always requires an Entra Easy Auth principal.

## 2. Prepare your own Fabric resources (explicit cloud operations)

Use a dedicated development workspace, not a customer production lakehouse.
The default deployment flag `FABRIC_DEPLOY_TEST_ONTOLOGY=0` makes **no Fabric
calls, token acquisition, generation or sample-state writes** and preserves
your explicit integration references. SQL-only and ontology-only configurations
are supported; leaving Fabric intentionally unconfigured retains ordinary
ungrounded real SDK chat. Fabric being enabled alone supplies none of these IDs
or permissions.

### Select existing resources from Settings

Use Settings' workspace discovery and validation controls to choose existing
readable EnterpriseKG Lakehouse/ontology items. No sample preparation or
infrastructure deployment is required for a browser selection. Choices are
account-local UUID preferences, not shared environment changes; Clear restores
the operator default. Every M4/M6 turn resolves the selection again before
inference. M7 and explicit demo mode retain their own sources.

The authenticated user needs existing-consented delegated Fabric access and,
when a Lakehouse is selected, separate SQL-audience access. The backend verifies
signed tokens against actual Entra tenant/object claims, not the platform
principal ID. Missing consent/scopes or item permissions are explicit failures;
do not grant new permissions, change identities or seed data to conceal them.
Metadata validation does not establish SQL query permission, ontology indexing,
AI entitlement or row provenance. See [MCP connection details](docs/MCP.md#per-account-fabric-workspace-selection).

### Guarded deployment sample (explicit opt-in)

Set nonsecret `FABRIC_TEST_WORKSPACE_ID` to an already approved development
workspace UUID and `FABRIC_DEPLOY_TEST_ONTOLOGY=1`, leaving `IQ_DEMO_MODE=0`.
Do not point this at a production workspace. The supported preparing command is
`azd up`, which runs the unchanged authority-key preflight before any sample
operation. Obtain and supply exact current keys privately as described in
section 4; do not generate replacements to unblock preparation.
Do not request tenant-wide Fabric Admin/`Tenant.Read.All` just to use the flag.
Without already approved complete inventory access, keep flag `0` and select
existing references, or prepare clearly labelled synthetic resources separately
under an approved process and reuse their references. Manually supplied data's
origin is not automatically inferred by deployment.

The hook prepares three dedicated `EnterpriseIQ_Test_<scope>_v1_*` items:
Lakehouse, Notebook and JSON-generation-1 Ontology. The notebook contains fresh
synthetic rows for all twelve tables (24 staff, 16 clients, 8 programmes,
48 projects; at most 3000 linked rows and a bounded encoded definition).
It creates only missing expected tables on initial/partial preparation, verifies
schema/count/content, and never overwrites or appends existing rows. A completed
sample rerun is verification-only; missing or changed tables fail, not reseed.

The provisioning operator must already have workspace Contributor-or-higher,
reviewed item read/write/metadata and notebook-execution scopes, supported
capacity and enabled Fabric/ontology preview tenant settings. Runtime read-only
SQL and delegated-user ontology search are separate authorizations. The current
ontology MCP preview documents a service-principal limitation. Successful item
creation is not proof of indexing, MCP entitlement or runtime access.
Notebook execution must support the published per-run Boolean parameter. The
adapter polls the actual accepted core job, then reads the documented
notebook-specific `beta=true` result endpoint for the exact verification
`exitValue`; absent support or results fail explicitly, not success-by-status.
No capacity, workspace, grants, consent, tenant switches or models are created.
For sample mode only, the selected workspace must be exclusively writable by
the submitting operator. Paginated workspace-role inventory requires existing
Member-or-higher and `Workspace.Read.All`/`Workspace.ReadWrite.All`; notebook
effective-access inventory uses the documented Fabric Admin preview API and
requires existing Fabric administrator or authorized service-principal access
and `Tenant.Read.All`/`Tenant.ReadWrite.All`. The hook grants nothing. Other/group
writers or delegators, unknown permissions, incomplete evidence, missing
operator write evidence or unsupported Admin API fail before notebook execution.
Definition and sample-lakehouse integrity are rechecked immediately before run.
Privileged tenant/capacity administrators and concurrent activity by the operator
remain trusted control-plane boundaries, not proof of perpetual exclusivity or
immutable execution. Default/0 performs none of these API/token/inventory checks.

Generated `FABRIC_TEST_*` references and ignored
`.azure\<environment>\fabric-test-state.json` contain nonsecret item IDs,
version/digests and operation/job references, never tokens or payload rows.
The state directory must be private to the authorized operator, not a shared
untrusted writable checkout. Reads/writes reject links/reparse-point ancestors;
state updates use exclusive unique temporary files and atomic replacement.
The hook does not create or change ACLs.
The exact original six `FABRIC_*` integration inputs are not rewritten.
`FABRIC_ACTIVE_*` Bicep outputs identify the selected runtime configuration;
`FABRIC_ACTIVE_DATA_MODE=test-sample` describes synthetic provenance, not health.
All fields are published before ready is set last; failure blocks continuation.

Retries reconcile recorded operations and verify server ownership markers,
recorded IDs/types, exact definitions/bindings and sample content. Name-only
collisions or missing ownership state are not adopted. No resource/definition
replacement, automatic upgrade or deletion occurs. Safe reads/explicit 429
rejections have bounded retries; ambiguous create/job transport failures require
operator resolution rather than blind resubmission. Creation LROs are bounded
to 10 minutes, notebook jobs to 30, SQL endpoint readiness to 10, and the whole
preparation to 45. Errors and expired deadlines never report success.
The notebook polling deadline reserves up to two minutes for cancellation of
only its actual recorded job. Accepted cancellation is tracked until a terminal
`Cancelled` status is confirmed; failure, timeout or completion races remain
explicit unknown state with the actual job preserved. Cancellation acceptance
alone does not prove compute stopped or eliminate charges. Do not submit a
replacement while an earlier job's outcome is uncertain.

If interrupted, keep the ignored state and rerun the same environment/version.
A `.lock` left by process termination requires an authorized operator to confirm
the original process is no longer running before removing that **specific**
lock file. Do not delete the state or markers to claim existing items. A
confirmed failed/cancelled job is archived with its actual ID and the hook fails.
A subsequent explicit rerun may submit a fresh guarded job; after the first
verified completion it remains no-create. Unknown status, timeout or missing
verification retains the actual job for reconciliation, never blind resubmission.
Uncertain write acceptance, drift or ownership collisions require inspection
and a separately reviewed recovery, not a flag to overwrite.

Standalone `azd provision`/preview validates already prepared state, never
creates samples. Standalone image-only `azd deploy` is also read-only for Fabric
and rejects mode/reference changes since the last provision. Image-only deploy
for flag-0 legacy environments with no active-selection outputs remains allowed
after local validation, with prior runtime selection unknown until reprovision.
Partial active outputs fail closed. Running
`azd hooks run preup` with flag 1 is itself an explicit mutating operation.
To select your original integration again, set the flag to 0 and reprovision
with current auth keys unchanged; old sample outputs are ignored, not deleted.

### Manual development helpers

The following older scripts are separate, explicitly mutating manual helpers;
they are **not** launched by the guarded deployment flow.
Set `FABRIC_WORKSPACE_ID`, `FABRIC_LAKEHOUSE_ID`, `FABRIC_SQL_ENDPOINT`,
`FABRIC_SQL_DATABASE=EnterpriseKG_Lakehouse`, and later `FABRIC_ONTOLOGY_ID`.
Authenticate to the intended tenant with Azure CLI. Use a **read-only SQL
principal** for runtime queries; provisioning permissions belong to a separate
operator identity.

1. `python create_lakehouse_tables.py --notebook` generates the twelve-table
   `_create_tables.ipynb`. Upload and attach it to your development lakehouse.
2. `python _gen_demo_data.py` generates fresh synthetic JSON under ignored
   `_demo_data_files`. Inspect the output; all sample identities use `.example`.
3. Upload those JSON files under `Files/demo`, then run `_load_demo_data.ipynb`.
   Alternatively `python _create_load_notebook.py` generates a notebook embedding
   only your freshly generated synthetic JSON. Loading **overwrites demo tables**.
4. `_create_and_run_tables_notebook.py`, `_create_and_run_load_notebook.py`,
   `_onelake_load.py`, `_create_ontology.py`, `_verify_ontology.py` and
   `_count_rows.py` are optional live helpers. Inspect them first; upload/run/create
   scripts write to the configured workspace. They are never run by unit tests.
5. Set `FABRIC_ONTOLOGY_MCP_URL` to the portal's actual ontology endpoint.
   Fabric ontology translation may require user-context AI entitlements.
   Web chat forwards `X-Fabric-Token` on **both** regular and streaming paths.

## 3. Configure inference and extra MCPs

Follow [BYOK.md](docs/BYOK.md), then [MCP.md](docs/MCP.md).
Set `IQ_DEMO_MODE=0` in both services for real Fabric. M6 requires
`PROJECT_ENDPOINT` and `MODEL_DEPLOYMENT_NAME`, optionally FAST/PRO deployments.
M7 additionally requires `M7_AGENT_NAME`. The existing
`agents/provisioning/provision_workiq.py` provisions a Foundry Work IQ connection
and agent only when explicitly run; it requires operator-selected tenant,
subscription/resource group/account and consented Work IQ OAuth credentials.
Work IQ availability/licensing is tenant-dependent.

## 4. Deploy (requires separate approval)

No deployment is needed for repository validation. When authorized, use
`azure.yaml` and `infra/main.bicep` via Azure Developer CLI. Bicep deploys:
internal-ingress MCP, external Next UI behind single-tenant Entra Easy Auth,
separate UI/MCP identities, ACR and log workspace. Bicep does **not** create Fabric
items, Foundry models or Work IQ consent. Only the explicit OFF-default `preup`
sample hook can prepare dedicated synthetic Fabric items as described above.
Set the parameters in `infra/main.parameters.json`.
BYOK key/token parameters are secure and referenced only by the MCP container;
the UI receives no provider credentials.

Use your secret manager/Key Vault-backed Container Apps secrets for production
rotation; avoid command-line secret literals and checked-in `.env` files.
Keep the backend app port private. Easy Auth must strip and replace principal
headers; application-level checks are not independent JWT validation.
Local auth bypass is rejected by production builds.

An approved test can use `IQ_DEMO_MODE=1`, `MCP_CPU=1.0`, an existing Azure model
deployment and renewable model authorization (`COPILOT_USE_AZURE_IDENTITY=1`).
Keep `COPILOT_API_KEY`/`COPILOT_BEARER_TOKEN` empty in this mode; grant only the
MCP identity the approved model-account invocation role. Synthetic IQ results
must not be represented as live Fabric/Foundry knowledge/M365 evidence.
Set an independent random `MCP_SERVICE_TOKEN` in the protected azd environment;
it is shared by both containers and never replaces delegated Fabric tokens.
See [infra configuration](infra/README.md) for authentication and sizing.

For the persisted Fabric test sample, instead keep `IQ_DEMO_MODE=0` and use
`FABRIC_DEPLOY_TEST_ONTOLOGY=1` plus the explicit sample workspace. These are
different modes: persisted sample tools call actual Fabric APIs over synthetic
rows; demo MCP fixtures do not establish live Fabric evidence.

Existing `azure.yaml` enables ACR remote builds, so no local Docker daemon is
required after registry provisioning. Do not bake the azd environment into
build contexts. Check new-environment core quota, image builds, SDK runtime,
ODBC installation, actual revisions and probe readiness. Then exercise
authenticated real SDK chat/SSE, MCP discovery and document/viewer journeys.
An anonymous redirect or API denial is expected, never a public demo bypass.

Uploaded documents are process-local and expire after 30 minutes or restart.
Keep UI at one replica (the supplied template does this). For multiple replicas,
replace the ephemeral store with an authenticated shared blob/metadata store
before scaling. A read-only source catalog can be mounted into a custom image;
configure `IQ_DOCUMENT_CATALOG`. Never expose arbitrary filesystem roots.

### Easy Auth authority rotation

Rotate only with explicit operator approval for the exact UI app/subscription.
Custom signing/encryption authority is separate from the Entra client secret,
MCP service token, managed identities and model credentials. Replacing it signs
existing users out; notify them to sign in again. Do not change tenant-wide
credentials, provider settings, roles, ingress or authorization policy.

1. Record a sanitized before snapshot: current auth configuration, active image,
   identity/ingress, and secret names, never values. Before rotation, hold one
   temporary verification control only in memory and confirm it can access the
   intended first-party authenticated API. Do not request a model response.
2. Generate independent platform-supported signing/encryption keys in memory or
   an approved secret manager, under new distinct UI-app secret names (maximum
   20 characters). Each key is exactly 32 cryptographically random bytes,
   serialized as canonical standard Base64 (44 characters), not hexadecimal or
   Base64url. Validate both with the credential-silent
   `infra\Assert-AuthKeyInputs.ps1` preflight in the protected deployment process.
   The platform must accept the actual authConfig binding; secret staging or a
   length-only check is not format/runtime proof. Preserve all existing secret entries/values, including
   Key Vault metadata. Never print or persist bearer tokens, key values, request
   headers/bodies, HARs, traces, screenshots or payload-bearing exceptions.
3. Update only the app's two custom key references:
   `encryptionSettings.containerAppAuthSigningSecretName` and
   `encryptionSettings.containerAppAuthEncryptionSecretName`. Preserve the
   remaining auth configuration. Follow the supported platform activation/
   restart procedure and verify actual healthy revisions; a successful update
   response is not proof that old credentials are rejected.
4. Against the exact HTTPS app origin, confirm the pre-change control is denied
   on the same authenticated API, then perform a new login and confirm the new
   session is accepted. Disable redirects for credential-bearing requests;
   never forward an app credential to Microsoft sign-in/static/foreign origins.
   Use fail-closed origin guards and suppress secret-bearing diagnostics.
5. Persist only the new reference names in the protected deployment environment:
   `WEBUI_AUTH_SIGNING_SECRET_NAME` and `WEBUI_AUTH_ENCRYPTION_SECRET_NAME`.
   On subsequent provision, supply the exact current values through required
   secure process inputs, retrieved privately by the authorized deployer from
   the app secret store/approved secret manager. Do not persist them to azd
   `.env`, print them, or regenerate keys during routine provisioning.
   Name-only updates are not a supported retention mechanism; see
   [reference preservation](infra/README.md#preserve-custom-easy-auth-authority).
   Read back the active references and verify unchanged non-key auth, secrets,
   image, identity, roles and resource allocation. Close owned verification
   contexts/processes and discard controls and credentials from memory.

Clearing a browser, closing a context or calling `/.auth/logout` is not proof of
bearer invalidation. Do not restore an old signing key as a security rollback:
that can re-enable previously issued credentials. Resolve a failed activation
with newly generated authority and verify old/new controls again under approval.

When an originally exposed credential is no longer available, successful
pre-change-control rejection proves rejection of that control under the old
current signing authority, not direct replay testing of every historical token.
Record that limitation and unknown recipient retention/replay explicitly.
Never infer old expiry from a newly minted token's claims. If old controls remain
accepted, new login fails, or unrelated settings drift, keep the security/merge
gate closed and report the failure; do not claim containment from key names alone.

Official references: [authConfig custom-key schema](https://learn.microsoft.com/en-us/azure/templates/microsoft.app/2025-01-01/containerapps/authconfigs)
and [Container Apps secret lifecycle](https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets).

## 5. Evaluations

```powershell
# No cloud calls: selector and schema smoke
python agents\tests\run_tests.py --methods M4 --dry-run
# Live BYOK inference (billed by your provider)
python agents\tests\run_tests.py --methods M4 --codes PL-B01 --timeout 180
# Live existing Foundry workflows (requires their credentials)
python agents\tests\run_tests.py --methods M6 M7 --codes PL-B01
```

`M4` is retained only as a wire/evaluation compatibility ID; it is not the old
direct Responses-API orchestration. SDK errors are explicit, cancellation aborts
the active session, and clients/sessions/MCP connections are torn down per turn.
M6/M7 still have their existing per-question deadlines; cancellation cannot
recall an already submitted Foundry request.

Live Fabric/Foundry/Work IQ and paid BYOK inference are **not** proven by mocked
SDK tests. Run tenant-specific entitlement, model/tool capability, firewall,
ODBC and consent checks after supplying credentials and approving live calls.
