# Server-side Copilot SDK BYOK

The pinned Python SDK is `github-copilot-sdk==1.0.15`. It uses
`CopilotClient(use_logged_in_user=False)`, a compatible stdio runtime, and
`create_session(model=..., provider=...)`. No direct LLM HTTP fallback exists.
The runtime/session API was verified against the installed package, rather
than relying on older SDK examples with dictionary constructor options.

| Variable | Meaning |
|---|---|
| `COPILOT_PROVIDER` | `openai`, `azure`, `anthropic` |
| `COPILOT_BASE_URL` | Required model endpoint, HTTPS except loopback local hosts |
| `COPILOT_MODEL` | Required deployed model name, not a UI-selectable key |
| `COPILOT_API_KEY` | Server secret; not `NEXT_PUBLIC_*` |
| `COPILOT_BEARER_TOKEN` | Optional server bearer token, takes precedence over key; must be refreshed by the operator |
| `COPILOT_USE_AZURE_IDENTITY` | `1` acquires a fresh/cached renewable Azure managed-identity token per turn; do not combine with a static key/token |
| `COPILOT_WIRE_API` | `completions` (default) or `responses` |
| `COPILOT_AZURE_API_VERSION` | Optional native Azure API version; absent uses SDK versionless route |
| `COPILOT_AZURE_RESOURCE_ID` | Approved Foundry account ARM resource ID for enabled deployment inventory |
| `COPILOT_LOCAL_NO_KEY` | Explicit `1` permits keyless loopback local provider |
| `COPILOT_CLI_PATH` | Optional compatible runtime executable path |

The SDK automatically provisions its matching runtime (currently `1.0.89`),
or prefetch it with `python -m copilot download-runtime`. A separately installed
interactive CLI is not automatically compatible: an incompatible override can
create sessions yet fail SDK teardown. Leave `COPILOT_CLI_PATH` unset by default.

**Microsoft Foundry**: `openai`, full
`https://<resource>.openai.azure.com/openai/v1/`, deployed model name,
`responses` for supported GPT deployments, server-side key.
**Native Azure OpenAI**: `azure`, `https://<resource>.openai.azure.com`,
deployment name, optional API version.
**OpenAI**: `openai`, `https://api.openai.com/v1`, key, available model.
**Anthropic**: `anthropic`, `https://api.anthropic.com`, key, available Claude model;
the SDK uses the Messages API irrespective of wire API.
**Ollama**: `openai`, `http://127.0.0.1:11434/v1`, locally installed tool-capable
model, `COPILOT_LOCAL_NO_KEY=1`.
**Foundry Local**: `openai`, the loopback URL reported by `foundry service status`,
locally available model, `COPILOT_LOCAL_NO_KEY=1`. The port is not fixed.

Choose a tool-capable model. Model availability, vision and quotas are provider
constraints, not GitHub subscription guarantees. Images use the SDK's blob
attachment API; failures are surfaced rather than silently dropping inputs.

For key-disabled Azure accounts, set `COPILOT_USE_AZURE_IDENTITY=1` in the MCP
container, assign its `AZURE_CLIENT_ID` identity the **Cognitive Services OpenAI
User** role on the approved model account, and leave key/bearer settings empty.
The asynchronous credential is closed after acquisition, refreshes tokens on
subsequent turns and runs inside the turn deadline. This mode only accepts
HTTPS Azure OpenAI/Foundry model hosts and never falls back to another identity
or a key. It uses the same supported SDK bearer-token provider configuration.
It is intended for Azure managed-identity hosting, not local CLI sign-in.

## Enabled deployments and per-user selection

Settings discovers deployments server-side using the configured account's
[ARM deployment-list API](https://learn.microsoft.com/rest/api/aiservices/accountmanagement/deployments/list?view=rest-aiservices-accountmanagement-2024-10-01),
`api-version=2024-10-01`, not the SDK's global model catalog or a hardcoded list.
Only `provisioningState=Succeeded`, nonzero capacity and compatible OpenAI chat
deployments are offered; the Responses wire requires its `responses` capability.
Embedding-only and incompatible formats are excluded. UI labels include the
actual deployment ID, underlying model and version when present.

The MCP managed identity acquires an ARM-audience token for this read-only
inventory in Azure, independently of static-key model authentication. Local
inventory may use `DefaultAzureCredential` with explicitly authorized developer
access; this does not enable local CLI authentication for model inference.
Cognitive Services OpenAI User includes
`Microsoft.CognitiveServices/*/read` at its assigned resource scope; no new role
is automatically granted. Missing discovery configuration/permission, empty
inventory and upstream failures are explicit, never replaced by a static list.
Safe metadata is cached for 30 seconds; Refresh and each selected-model execution
fetch current deployment state. This is inventory, not a paid inference probe.

The selected deployment is stored as a nonsecret, authenticated-account-scoped
browser preference and passed into each SDK session without mutating the global
server model. A stale/disabled/incompatible selection fails; the server default
is used only when the user has not selected one. No browser provider-endpoint,
resource-ID, wire API or credential override is accepted. M6/M7 remain separate.
Model and grounding metadata are returned consistently by regular and SSE chat.

## Local configuration and demo-label meaning

Follow the [README PowerShell steps](../README.md#provider-setup) for securely
entering a key in the backend terminal. Root `.env` is loaded after process
environment, `agents/tests/.env`, and `agents/.env`; restart the backend and
remove stale overrides. Do not quote values in this loader. UI `.env.local`
contains only UI/server-proxy configuration, never model credentials.

The former "DEMO: synthetic MCP data only..." banner was static guidance, not a
missing-provider diagnosis. `IQ_DEMO_MODE=1` enables explicit local synthetic
grounding fixtures, but chat still runs real BYOK inference and preserves errors.
Hosted/default configuration is `IQ_DEMO_MODE=0`. Without connected real data
tools, SDK chat is explicitly marked ungrounded and cannot claim Fabric/M365
access or manufacture citations.

Each turn uses an empty temporary cwd/base directory, disables config/skill/file
hook discovery, allows only application-registered tools and denies requests
for shell/filesystem/network permissions. Provider exception text is never
relayed to the browser/evaluation traces. Missing settings and runtime failures
are explicit. SSE emits deltas, progress, one final result or an error.
Abort/timeout disconnects the session, stops the client and closes MCP transports.

### Live query activity

The UI displays **Copilot SDK** (not the internal `M4` ID) from the first
session line onward. `M4` remains unchanged in API requests/results, evaluations
and saved history. The regular and SSE routes still use the same runner.

Progress uses the pinned SDK 1.0.15 public `copilot.session_events` schema:
`assistant.turn_start` counts model steps, `assistant.intent` signals activity
without exposing its model-authored text, and `assistant.turn_retry` reports an
actual model retry without relaying its reason. Registered tools are correlated
by `tool_call_id` from `tool.execution_start` and `tool.execution_complete`;
completion uses the SDK's `success` flag, not a fabricated stage or inferred
data access. Namespaced remote tools retain their original server/tool labels.
Arguments, result bodies, raw errors and reasoning events are not progress.
Repeated event IDs are deduplicated in a bounded 256-event window.

The polite, atomic status headline is separate from streamed answer text.
**Activity details** contains only safe status/tool identifiers, never a JSON
payload dump. The UI retains at most 40 deduplicated activity entries plus the
untruncated answer buffer. Completion, failure, interruption and cancellation
remain explicit; the final SSE result/error is authoritative. Elapsed time is
only a clock, not a source of progress stages.

A failed ontology call is not labelled a retry or successful grounding. A later
lakehouse call is displayed only if the SDK actually starts it. This feature
does not require ontology-first routing, enable tools, change Work IQ or
ontology gates, or alter model/persona/account selection. Tool completion does
not prove that returned evidence answers the question.

Each SDK turn receives an opaque, server-generated request ID, shown under
**Activity details** and retained under **Response details**, including partial
answers. The ID is returned in regular/SSE result metadata and the start trace.
The existing server logger records that ID with requested/configured/offered
ontology flags, configured/offered lakehouse flags, known native Fabric tool
start/completion/failure, model steps/retries and final/failure/cancellation.
Configured means effective configuration is present, not a connectivity or
permission check; offered means the tool was registered for this session, not
that it ran. Logs cap activity at 128 records per turn (with a limit marker),
while retaining terminal states. No prompts, SQL, arguments/results, raw intent,
reasoning/errors, identities, URLs or credentials are logged by this progress
path. This adds no telemetry service or environment setting. Previously deployed
versions without these logs cannot retrospectively establish tool routing; a
separately approved source/runtime rollout is needed to use the new diagnostics.

The retained synchronous Foundry M6/M7 paths cooperatively stop subsequent
model/tool steps and close their HTTP clients on disconnect; each model request
has a 60-second transport timeout without automatic retries. Already-submitted
cloud work may still finish (and incur charges); cancelling the browser is not a
guarantee of cloud-side cancellation. M6 strategist threads inherit the caller's
delegated identity context.
