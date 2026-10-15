import Link from 'next/link';

const foundrySettings = `COPILOT_PROVIDER=openai
COPILOT_BASE_URL=https://<resource>.openai.azure.com/openai/v1/
COPILOT_MODEL=<tool-capable-deployment-name>
COPILOT_WIRE_API=responses
COPILOT_USE_AZURE_IDENTITY=0
IQ_DEMO_MODE=0`;

const startBackend = `$key = Read-Host "Model provider API key" -AsSecureString
$env:COPILOT_BEARER_TOKEN = ""
$env:COPILOT_API_KEY = [System.Net.NetworkCredential]::new("", $key).Password
Remove-Variable key
.\\.venv\\Scripts\\Activate.ps1
python -m enterprise_mcp --http --host 127.0.0.1 --port 8765`;

const linkStyle = 'inline-flex min-h-6 items-center rounded-sm underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-grey';
const headingStyle = 'font-display text-xl font-bold';
const codeStyle = 'overflow-x-auto rounded-lg bg-brand-grey p-4 text-sm text-brand-paper';

export default function ProviderSetupPage() {
  return (
    <main className="mx-auto max-w-3xl space-y-6 px-4 py-6 text-sm leading-relaxed">
      <Link href="/" className={linkStyle}>Back to chat</Link>
      <h1 className="font-display text-3xl font-bold">Provider setup</h1>

      <section aria-labelledby="demo-sources" className="space-y-3">
        <h2 id="demo-sources" className={headingStyle}>Demo sources, real model responses</h2>
        <p>
          The earlier banner, &quot;DEMO: synthetic MCP data only. Copilot BYOK inference still requires a
          configured provider.&quot;, was static guidance, not a detected provider failure.
          IQ_DEMO_MODE=1 supplies synthetic Fabric IQ portfolio, Foundry IQ documents and Work IQ
          collaboration evidence. A configured Copilot SDK BYOK provider generates real model responses;
          the evidence is not live Fabric or Microsoft 365 data.
        </p>
        <p>
          This help page does not check provider readiness. Only an explicit chat error indicates a
          failed turn. Missing configuration and provider/runtime failures remain visible; demo mode
          does not fabricate a successful model answer.
        </p>
      </section>

      <section aria-labelledby="hosted-users" className="space-y-3">
        <h2 id="hosted-users" className={headingStyle}>Using Agent Manager and the hosted app</h2>
        <ol className="list-decimal space-y-2 pl-5">
          <li>In Settings, choose <strong>Copilot SDK BYOK</strong>. The existing test deployment has an operator-configured model provider.</li>
          <li>Open <strong>Agent Manager</strong> from the sidebar or Settings. Select/add a persona, add/edit/enable its skills, and save its system prompt. These instructions apply to subsequent SDK turns.</li>
          <li>In <strong>Agent Manager → MCP Servers</strong>, inspect/enable operator-managed tools or add the URL of a real operator-approved HTTPS Streamable HTTP or legacy SSE service and select its approved authentication reference. Test connection and discover tools, save it, then enable it. Skills and MCP controls are available only in Agent Manager; there are no automatically enabled cloud demo servers.</li>
          <li>In Settings, refresh enabled models, select a deployment and Save model. The list comes from the authorized Foundry account; the selected deployment is checked again at execution.</li>
          <li>Ask a general question or attach a document. Until real grounding is connected, answers are explicitly labelled &quot;Ungrounded model response&quot;, not presented as live Fabric/M365 evidence.</li>
        </ol>
        <p>
          Hosted users do not need local environment variables, API keys, a GitHub Copilot subscription,
          or copilot login. If chat reports an error, give the explicit error to the operator.
          Council and Work IQ agent modes have separate Foundry prerequisites; they are not the demo SDK path.
        </p>
        <p>
          Agent definitions and model choices are saved for your account in this browser/profile only,
          not on the server or across devices. Backend restarts do not erase them, but clearing browser
          data does. Only nonsecret fields and credential references belong there. Provider setup
          opens in a separate tab so your active chat is not discarded.
        </p>
        <p>
          The operator approves exact service origins, safe tool names and origin-bound server
          credential profiles. An empty approved-origin list means no external service is connected:
          ask the operator to approve your actual service, not a demonstration replacement.
          Browser-entered commands, raw authentication headers, keys and tokens are not accepted.
          Application managed identity is not delegated user authentication; generic OAuth/M365
          consent is not implemented in this panel.
        </p>
      </section>

      <section aria-labelledby="local-operators" className="space-y-3">
        <h2 id="local-operators" className={headingStyle}>Operator setup: local PowerShell</h2>
        <p>
          Use Python 3.11+ and Node 22.13+. From the repository root, create a .venv with
          <code> python -m venv .venv</code>, activate it with
          <code> .\.venv\Scripts\Activate.ps1</code>, install with
          <code> python -m pip install -e &quot;.\mcp[dev]&quot;</code>, and copy
          .env.example to .env and webui\.env.example to webui\.env.local if not already configured.
          Do not overwrite an existing configuration.
        </p>
        <p>
          Edit the root .env with your own endpoint and deployed tool-capable model. For Microsoft
          Foundry&apos;s OpenAI v1 endpoint and a deployment supporting the Responses API:
        </p>
        <pre className={codeStyle} tabIndex={0} aria-label="Foundry server configuration"><code>{foundrySettings}</code></pre>
        <p>
          For OpenAI, use COPILOT_PROVIDER=openai, COPILOT_BASE_URL=https://api.openai.com/v1,
          your available tool-capable COPILOT_MODEL, and COPILOT_WIRE_API=completions
          (or responses if supported). For native Azure OpenAI, use provider azure with
          https://&lt;resource&gt;.openai.azure.com and its deployment name; do not mix the native
          Azure route with the OpenAI v1 route.
        </p>
        <p>
          Leave COPILOT_API_KEY and COPILOT_BEARER_TOKEN empty in files; enter a key securely in the
          backend terminal below. Clear any stale bearer token first because it takes precedence over
          a key. Never put provider credentials in the browser, webui\.env.local, NEXT_PUBLIC_*,
          screenshots, or Git.
        </p>
        <pre className={codeStyle} tabIndex={0} aria-label="Start the backend with a securely entered key"><code>{startBackend}</code></pre>
        <p>
          The MCP loader reads the root .env. Process environment values take precedence, followed by
          agents\tests\.env, agents\.env, then the root .env; remove stale overrides.
          Restart the backend after changes. A key entered above is held only in that terminal&apos;s
          environment; enter it again in a new terminal.
        </p>
        <p>
          In webui\.env.local set MCP_BASE_URL=http://127.0.0.1:8765 and IQ_DEMO_MODE=0.
          In a second terminal run <code>Set-Location webui</code>, <code>npm ci</code>, then{' '}
          <code>npm run dev -- --hostname 127.0.0.1</code>. Restart the UI after environment changes.
          For a non-loopback MCP service, use the same server-only MCP_SERVICE_TOKEN in both services.
        </p>
      </section>

      <section aria-labelledby="azure-identity" className="space-y-3">
        <h2 id="azure-identity" className={headingStyle}>Operator setup: Azure managed identity</h2>
        <p>
          For an Azure-hosted MCP backend only, use the approved Azure endpoint/deployment and set
          COPILOT_USE_AZURE_IDENTITY=1. Leave COPILOT_API_KEY and COPILOT_BEARER_TOKEN unset or empty.
          The MCP container&apos;s AZURE_CLIENT_ID selects the assigned managed identity; it needs
          Cognitive Services OpenAI User on the approved model resource. Restart/redeploy the backend
          after configuration changes. This is not local az login authentication.
          The configured hosted test app already uses this path; its users need not change it.
        </p>
        <p>
          Set COPILOT_AZURE_RESOURCE_ID to the approved model account&apos;s ARM resource ID for
          deployment discovery. Existing Cognitive Services OpenAI User grants include
          Microsoft.CognitiveServices/*/read; if your identity lacks deployments/read, request a
          resource-scoped permission review rather than silently substituting a global model catalog.
          Only successfully provisioned chat deployments compatible with the configured API are offered.
        </p>
      </section>

      <section aria-labelledby="troubleshooting" className="space-y-3">
        <h2 id="troubleshooting" className={headingStyle}>Troubleshooting explicit errors</h2>
        <dl className="space-y-3">
          <div><dt className="font-bold">Set COPILOT_MODEL and COPILOT_BASE_URL</dt><dd>Configure the MCP backend, not the UI. Check environment precedence and restart it.</dd></div>
          <div><dt className="font-bold">Set server-side COPILOT_API_KEY or COPILOT_BEARER_TOKEN</dt><dd>Supply a backend credential or, for Azure hosting, the managed-identity configuration above.</dd></div>
          <div><dt className="font-bold">Copilot SDK runtime failed / session failed</dt><dd>Use the pinned github-copilot-sdk==1.0.15 and its matching runtime. Leave COPILOT_CLI_PATH unset by default; prefetch with python -m copilot download-runtime. Also check provider endpoint, wire API, deployment, permission, quota and network access. A runtime error alone does not prove a missing key.</dd></div>
          <div><dt className="font-bold">MCP unauthorized or unavailable</dt><dd>Check MCP servers health, MCP_BASE_URL and the shared MCP_SERVICE_TOKEN separately from model credentials. Live Fabric and M365 access have their own authentication/licensing requirements.</dd></div>
          <div><dt className="font-bold">Unapproved MCP origin / authentication reference</dt><dd>The operator must approve that exact origin, tools and credential profile. Private/metadata/loopback endpoints and redirects are rejected by default. Save unapproved definitions disabled; never work around the trust policy.</dd></div>
          <div><dt className="font-bold">Empty catalog / selected model no longer enabled</dt><dd>Refresh enabled models and check the approved ARM resource, deployments/read permission, provisioning state and wire compatibility. Choose an available deployment; there is no silent fallback.</dd></div>
          <div><dt className="font-bold">Synthetic sources in local tests</dt><dd>IQ_DEMO_MODE=1 is an explicit local fixture option only. It supplies no live Fabric/M365 evidence and still needs real inference. Hosted defaults are IQ_DEMO_MODE=0.</dd></div>
        </dl>
      </section>

      <nav aria-label="Further setup documentation" className="flex flex-wrap gap-4 border-t border-brand-line pt-4">
        <a className={linkStyle} href="https://github.com/PaulTozer/Fabric-Foundry-GitHubCopilot-IQ-Accelerator-OpenSource/blob/main/README.md#provider-setup">README provider setup (GitHub)</a>
        <a className={linkStyle} href="https://github.com/PaulTozer/Fabric-Foundry-GitHubCopilot-IQ-Accelerator-OpenSource/blob/main/docs/BYOK.md">Full BYOK reference (GitHub)</a>
      </nav>
      <p>The instructions above are built into this app; GitHub access is only needed for the optional reference links.</p>
    </main>
  );
}
