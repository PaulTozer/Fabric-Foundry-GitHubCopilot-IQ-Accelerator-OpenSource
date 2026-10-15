'use client';
import { useState } from 'react';
import { getFabricAccessToken } from '@/lib/fabric-token';

interface ServerInfo {
  id: string; transport: string; enabled: boolean; demo: boolean;
  tools: { name: string; description?: string }[]; error: string;
}
export function McpServers({ disabled, onChange, pending, fabricTokenRequired }: {
  disabled: string[]; onChange: (ids: string[]) => void; pending: boolean; fabricTokenRequired: boolean;
}) {
  const [servers, setServers] = useState<ServerInfo[] | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  async function refresh() {
    setLoading(true); setError('');
    try {
      const headers: Record<string, string> = {};
      if (fabricTokenRequired) headers['X-Fabric-Token'] = await getFabricAccessToken();
      const response = await fetch('/api/mcp', { headers });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error ?? 'Registry failed.');
      setServers(result.servers);
    } catch (err) { setError(err instanceof Error ? err.message : 'MCP discovery failed.'); }
    finally { setLoading(false); }
  }
  return <details className="border-b border-brand-line bg-white px-4 py-2">
    <summary className="cursor-pointer font-semibold">Operator-managed MCP servers</summary>
    <p className="my-2 text-xs">Server definitions and credentials are operator-managed. These per-browser toggles apply to Copilot SDK turns. Work IQ additionally requires the Work IQ toggle.</p>
    <button type="button" onClick={refresh} disabled={loading || pending} className="rounded-sm border px-3 py-1 text-sm">
      {loading ? 'Checking...' : 'Refresh tools and health'}
    </button>
    {error && <p role="alert" className="text-red-700">{error}</p>}
    {servers?.length === 0 && <p>No operator-managed additional MCP servers configured.</p>}
    {servers?.map(server => <div key={server.id} className="my-2 rounded-sm border p-2 text-sm">
      <label className="flex gap-2">
        <input type="checkbox" checked={server.enabled && !disabled.includes(server.id)}
          disabled={!server.enabled || pending}
          onChange={event => onChange(event.target.checked
            ? disabled.filter(id => id !== server.id) : [...disabled, server.id])} />
        {server.id} · {server.transport} · {server.demo ? 'DEMO / synthetic' : 'live'}
        {server.error ? ' · unavailable' : server.enabled ? ' · healthy' : ' · operator-disabled'}
      </label>
      {server.error && <p role="alert" className="text-red-700">{server.error}</p>}
      <ul className="mt-1 pl-5">{server.tools.map(tool =>
        <li key={tool.name} title={tool.description}>{tool.name}</li>)}</ul>
    </div>)}
  </details>;
}
