# Multiple MCP servers

The source Fabric MCP remains a real internal FastMCP server: SQL analytics,
ontology discovery/search, evaluations, crowd tools and agent invocation.
Copilot SDK additionally discovers operator-configured servers via the Python
MCP SDK, exposes namespaced tool handlers (`server-id__tool`) and closes all
connections after each turn. Required enabled servers fail the turn explicitly
when unavailable; they are never silently skipped.

The UI-to-backend HTTP boundary uses a separate `MCP_SERVICE_TOKEN` secret in
both server environments. Every `/registry`, `/ask`, `/ask/stream` and `/mcp`
request requires `X-IQ-Service-Token` when configured. The UI constructs this
header from server configuration, never from browser input; delegated Fabric
Authorization remains independent. Non-loopback HTTP startup refuses a missing
secret, and production UI refuses to proxy without one. Only `GET /healthz`
is exempt; it returns a constant status without tools, configuration or IDs.
Loopback development may omit the secret. Direct HTTP MCP clients must supply
the service token through an operator-managed environment header reference.

Set `IQ_MCP_CONFIG` to an operator-owned JSON file matching
`mcp/servers.example.json` for explicitly shared operator catalog entries.
These definitions, especially stdio commands, cannot be supplied from the browser.
User-owned remote definitions are separately validated as described below.

* `http`: Streamable HTTP with initialization/session lifecycle and no redirects.
* `sse`: Legacy SSE, initialized client session, no redirects.
* `stdio`: Absolute executable path, argument array and **explicit `trusted:true`**.
  Never use downloaded/user-provided executable definitions without review.
* `enabled:false`: Operator-disabled; not connected or routable.
* `tools`: Optional allowlist. Avoid exposing recursive `ask_agent` on the
  same accelerator server.
* `allowedOrigins`: Explicit server-origin grant for HTTP/SSE; HTTPS is
  mandatory except loopback. The operator owns DNS/network trust; this is not a
  generic public URL-fetching API. Do not grant metadata/internal admin hosts.
* `headers`: Header name to environment-variable name; put complete
  `Bearer ...` value in that server environment reference.
* `env`: Child process variable to parent environment reference. Secrets are
  not inherited implicitly by stdio demo servers.
* `useFabricToken:true`: Uses the forwarded signed-in user's Fabric token;
  never stores it in browser settings or the registry file.
* `ontology:true`: Omit this server when the ontology tool toggle is off.

The **MCP servers** panel shows transport, enabled status, per-server tools and
health failures. Per-turn/browser opt-out never grants new hosts/commands.
`work-iq` tools additionally require the Work IQ opt-in. M6/M7 preserve their
existing integrations and do not silently switch to the new registry.

## Chat settings compatibility

### Per-account Fabric workspace selection

Settings exposes **Fabric workspace ID**, **Discover workspace items**,
**Validate and save workspace**, and **Clear workspace / use operator default**.
Discovery uses official read-only metadata for the explicit workspace under the
current user's delegated Fabric identity. Select a real Lakehouse and/or
generation-1 Ontology; multiple candidates require an explicit choice. Lakehouse
SQL must be provisioned and expose the canonical EnterpriseKG `ENT_*` table
names. This does not add arbitrary-domain schemas or generation-2 support.

Only nonsecret UUID selections persist in the existing account-partitioned
browser preferences. They survive reloads, not browser-data clearing, and are
not shared across accounts/devices or written to backend environment variables.
Unsaved edits leave the previous selection active. Clear restores the operator's
unchanged default. M4/M6 use the selection; M7 remains Work IQ and does not.
Sending waits for successfully loaded preferences. Workspace token acquisition,
save and clear compare the current authenticated owner with the loaded namespace;
an account switch requires reloading that account's preferences.

Each regular/SSE turn revalidates readable workspace/item metadata before model
execution. Fabric metadata/ontology and SQL require **different delegated
audiences**. MSAL uses existing consent only, binds the account to verified Entra
`tid`/`oid`, and keeps application token caches in memory by account/audience.
SQL needs existing-consented `https://database.windows.net/.default` access;
Fabric needs existing-consented `https://api.fabric.microsoft.com/.default`.
Unavailable tokens, consent, metadata scopes or item permissions fail explicitly.
The feature grants nothing and never uses the container identity as a fallback
for a selected workspace. Selecting ontology only avoids the SQL audience.
SDK tool callbacks retain the originating verified connection and credentials,
including callbacks dispatched from the SDK reader thread.

The gateway reads Entra claims from the trusted Easy Auth principal; its platform
principal ID is used for the existing preference namespace, **not** assumed to be
an Entra object ID. Backend token signature/issuer/audience/expiry and delegated
subject are checked against those trusted claims. Forwarded browser owner/tenant
headers are not authoritative. API ports must remain behind the existing Easy
Auth/service-token boundary.

Metadata validation is not a row, query-permission, indexing or AI-entitlement
test. The banner reports a selected workspace, not proven live data access.
Sample markers cause a conservative synthetic label; they do not attest to row
contents or a completed provisioning lifecycle. Any marked selected item keeps
the synthetic warning, including mixed selections. Reloaded UUIDs initially
show unknown provenance; successful per-turn metadata validation restores it.
Selected-workspace errors never
fall back to operator data. No tenant/admin inventory, samples, notebook runs,
grants, capacities or resources are created.

The `/api/fabric/workspace` proxy and internal `/fabric/workspace` endpoint share
authenticated service/owner and discovery rate limits. Pagination is restricted
to the selected official collection, bounded to five pages/500 entries/20 calls;
responses are capped at 2 MiB and discovery has a 40-second deadline. Credentials
never follow metadata redirects. Direct MCP `ask_agent`, `query_lakehouse`,
`query_ontology` and `describe_ontology` also accept optional `fabric_selection`
UUID objects under authenticated HTTP identity; no-selection/stdio calls retain
operator defaults. Legacy resource URIs are operator resources, not a mechanism
to persist user selections; selected-context metadata bypasses the operator cache.

The settings menu contains agent method, ontology and Work IQ controls.
The RIBA Plan of Work feature, stage curriculum, classifier, automatic brief
injection and dedicated tool are removed. Existing browser stage preferences
are cleaned without resetting the remaining settings or touching skills/chats.
Legacy stage request fields are ignored by both regular and streaming chat;
`/stage N` is ordinary question text, not a control command. Independent
project-stage fields in the Fabric schema/data remain unchanged.

## Agent Manager: user-owned remote connections

Agent Manager has only functional Skills, System Prompt and MCP Servers sections,
plus persona selection. Add/edit/delete/enable a skill; save the prompt; add/edit/
remove/enable a real remote MCP connection; test it to inspect its tools and
connection errors. Enabled skills/prompts apply to every subsequent M4 turn.
Skills and MCP server controls are available only in Agent Manager, including
operator-managed tool discovery/enablement. There is no separate chat library,
composer skill picker or top-level MCP dropdown. These changes do not reset
active conversations or grant SDK filesystem/shell permissions.

The six shipped enterprise-consultancy skills are listed in every persona's
Skills tab: transport pursuit qualification, water proposal review, net-zero
carbon assessment, digital delivery, margin recovery and capability/resourcing.
They are **disabled until explicitly selected**; enabling every specialist
prompt by default would mix incompatible tasks. Built-ins cannot be deleted
from the catalog, but can be disabled or edited for that account/persona.
Saved prompt edits and disabled choices take precedence over shipped definitions.
The catalog is compiled into the UI, not discovered from desktop/CLI skills.

Fresh accounts start with **Enterprise Consultancy Analyst** and the actual
packaged [SDK instructions](../agents/CopilotSDK_Instructions.md), returned
by the authenticated configuration endpoint as `defaultSystemPrompt`.
Existing personas, including intentionally blank prompts/skill selections,
are not overwritten. **Use enterprise analyst prompt** loads that same prompt
into the editor; **Save system prompt** is required to apply it. Additional
custom personas still start blank. The backend always retains its application
grounding/safety instructions and appends only explicit persona overrides and
enabled skills for both regular and SSE requests. Missing/invalid packaged
instructions are an explicit configuration error, never a generic fallback.
Fresh initialization requires the UI and MCP backend versions that implement
`defaultSystemPrompt`; update both images together in a separately approved
deployment. Valid saved personas still hydrate if management configuration is
temporarily unavailable, without silently resetting their choices.

Nonsecret definitions/model choices persist in localStorage under an account
namespace derived from the signed-in tenant/object ID. They survive refresh and
container restarts but are **browser/profile-only**, not cross-device/server
storage. Clearing browser data deletes them. Never paste secret values into
names, prompts or URLs. Raw headers/tokens/keys are neither accepted nor persisted.
Fresh defaults are saved in that namespace on first successful load. The older
unscoped `enterpriseiq:skills:v1` library is not automatically imported: it has
no authenticated owner and could expose another account's custom prompts.

The UI gateway derives owner context from trusted Easy Auth ingress, never a
browser owner field, and constructs the internal service-token headers. Its
configuration/model/discovery APIs require authentication; mutations reject
cross-site origins. Each request supplies a bounded agent snapshot that the
backend validates anew; no shared mutable user definitions or credentials are
cached. Regular and SSE M4 turns use the same snapshot and selected model.
M6/M7 reject these overrides rather than pretending they are connected.

### Operator trust and authentication policy

`IQ_MCP_TRUST_POLICY` is a server-only JSON value with exact origins, approved
credential reference IDs, and reviewed tool names:

```json
{"origins":[{"origin":"https://approved-knowledge.example","authRefs":["public","knowledge-header"],"tools":["search_knowledge","get_document"]}]}
```

Examples are placeholders, not live services. The default policy is empty;
users can save a disabled draft, but cannot connect/enable an unapproved origin.
An empty tool grant exposes no remote tools. User tool allowlists may restrict
the operator grant further, never widen it. Tool descriptions/results are
untrusted evidence, not permission grants.

`IQ_MCP_AUTH_REFERENCES` defines operator credential profiles. For example:

```json
[{"id":"knowledge-header","label":"Approved knowledge service","type":"header","header":"Authorization","env":"KNOWLEDGE_MCP_AUTH"}]
```

The referenced backend secret contains the complete header value (for example
Bearer authentication), managed through a server environment/secret reference,
not a browser input. UI users see only ID/label/type and origin grants, never
the value, environment name or token. Internal `MCP_SERVICE_TOKEN` and model
credentials cannot be remote MCP profiles. Only explicitly shared operator
profiles are selectable; a user's definition cannot read arbitrary env vars.

A profile with `type:"managed-identity"` and a fixed operator-approved HTTPS
`scope` ending `/.default` acquires an application-identity token on connection.
It needs the actual service's narrow authorized role. This is **not delegated
user access**. `public` sends no credential and is permitted only when the
operator grants it for a genuinely public service. Generic OAuth/Graph/M365
consent is not implemented in this panel. Existing Fabric delegated tooling
remains separately configured and uses the caller's Fabric token; do not claim
M365 connected merely because a managed profile exists.

Remote user connections require HTTPS Streamable HTTP or actual legacy SSE.
Every connection validates DNS and pins the validated address while preserving
TLS hostname verification. Private, loopback, link-local, metadata and nonglobal
addresses are denied by default, including mixed public/private answers.
Intentional internal service grants require an exact origin and exact
`allowedIPs` list; do not grant generic internal CIDRs. Redirects and SSE callback
origin changes are blocked; advertised same-origin SSE message paths are checked
by the same transport. Proxy environment variables cannot bypass this boundary.
Loopback HTTP is available only to explicitly configured local transport tests,
not the production environment.

Compressed remote responses are refused; the service must honor
`Accept-Encoding: identity`, so decompression cannot bypass the byte limit.
Operator and user connection IDs must not collide. SDK tool aliases are bounded
to the provider's 64-character limit and retain exact server/tool routing.

Discovery initializes and lists tools without executing them. Each tool metadata
set is bounded to 64 tools/200 KB; remote response streams to 2 MB; discovery and
calls have deadlines. Discovery is limited to six requests per account/minute,
other management/turn requests to 60, with eight concurrent requests per backend
replica. Clients/transports are closed on cancellation/error. Health/auth errors
are explicit and never include credential values or transport exception text.

## Demo vs real integrations

`IQ_DEMO_MODE=1` starts three distinct real stdio FastMCP servers:
`fabric-iq` (synthetic portfolio), `foundry-iq` (knowledge search/get-document with
source/page citations) and `work-iq` (synthetic collaboration evidence).
Every response carries `simulation_only`. These are **explicit local test
fixtures**, not the cloud default/workflow. Defaults and hosted settings are
`IQ_DEMO_MODE=0`; demo chat still needs the real SDK and a BYOK/local model.
No connected real data tools means an explicitly ungrounded model response.

For **real Fabric IQ**, use the existing SQL/ontology tools, or the actual
Fabric portal ontology MCP endpoint with `search_ontology` and delegated token.
For **real Foundry IQ**, supply an approved, deployed knowledge MCP service;
this repository does not invent a Microsoft-hosted endpoint or entitlement.
For **real Work IQ**, the retained legacy M7 API/evaluation path uses Foundry's
`work_iq_preview` connector; M7 is no longer a user-facing method choice.
An additional MCP Work IQ bridge must be deployed and authenticated by the
operator; disabled example URLs are intentionally placeholders, not working
integrations. Map its source IDs to [document catalog entries](DOCUMENTS.md).
The **Work IQ** Settings switch is the SDK opt-in for the registry entry named
`work-iq`. Switching it off excludes those tools; switching it on still respects
disabled servers and does not grant consent, entitlement or a live connection.
Council does not use Work IQ. Old saved M7 selections migrate to SDK without
changing the explicit toggle value; an absent value stays off.

An approved stdio example:

```json
{
  "servers": [{
    "id": "knowledge",
    "type": "stdio",
    "enabled": true,
    "trusted": true,
    "command": "C:\\Tools\\node.exe",
    "args": ["C:\\Approved\\knowledge-server.mjs"],
    "env": {"SERVICE_TOKEN": "KNOWLEDGE_MCP_TOKEN"},
    "tools": ["search_knowledge", "get_document"]
  }]
}
```
