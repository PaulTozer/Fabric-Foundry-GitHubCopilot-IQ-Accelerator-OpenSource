'use client';

import { useEffect, useRef, useState } from 'react';
import { McpServers } from './McpServers';
import {
  availableAgentSkills, newAgent, validateAgent, type AgentDefinition, type AgentPreferences, type AgentSkill,
  type OperatorConfiguration, type RemoteServer, type ServerHealth,
} from '@/lib/agent-config';
import { SEED_SKILLS } from '@/lib/skills-seed';

const builtinIds = new Set(SEED_SKILLS.map(skill => skill.id));

const control = 'min-h-9 rounded-lg border border-brand-line bg-white px-3 py-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-grey';
const input = `${control} w-full`;

export function AgentManager({ preferences, configuration, onChange, onClose,
  disabledMcpServers, onDisabledMcpServersChange, pending }: {
  preferences: AgentPreferences; configuration: OperatorConfiguration | null;
  onChange: (value: AgentPreferences) => void; onClose: () => void;
  disabledMcpServers: string[]; onDisabledMcpServersChange: (ids: string[]) => void; pending: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [tab, setTab] = useState<'skills' | 'prompt' | 'servers'>('skills');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [editingSkill, setEditingSkill] = useState<AgentSkill | null>(null);
  const [editingServer, setEditingServer] = useState<RemoteServer | null>(null);
  const [originalServerId, setOriginalServerId] = useState<string | null>(null);
  const [serverTools, setServerTools] = useState('');
  const discovery = useRef<AbortController | null>(null);
  const [health, setHealth] = useState<ServerHealth[]>([]);
  const [testing, setTesting] = useState(false);
  const agent = preferences.agents.find(item => item.id === preferences.selectedAgent)!;
  const skills = availableAgentSkills(agent);
  const [prompt, setPrompt] = useState(agent.systemPrompt);
  const [name, setName] = useState(agent.name);

  useEffect(() => {
    const node = dialog.current;
    const previous = document.activeElement;
    node?.showModal();
    return () => {
      node?.close();
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
      else document.querySelector<HTMLButtonElement>('[aria-label="Settings"]')?.focus();
    };
  }, []);
  useEffect(() => {
    setPrompt(agent.systemPrompt); setName(agent.name);
    setEditingSkill(null); setEditingServer(null); setHealth([]);
  }, [agent.id, agent.name, agent.systemPrompt]);
  useEffect(() => {
    discovery.current?.abort();
    setTesting(false); setHealth([]);
    return () => discovery.current?.abort();
  }, [agent.id, agent.servers, editingServer?.id, editingServer?.url, editingServer?.type, editingServer?.authRef, serverTools]);

  function update(next: AgentDefinition) {
    setError(''); setNotice('');
    try {
      const checked = validateAgent(next);
      onChange({ ...preferences, agents: preferences.agents.map(item => item.id === checked.id ? checked : item) });
      setNotice('Saved in this browser/profile. Changes apply to the next Copilot SDK turn.');
      return true;
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not save agent.'); return false; }
  }
  function select(id: string) {
    try { onChange({ ...preferences, selectedAgent: id }); setError(''); setNotice(''); }
    catch (err) { setError(err instanceof Error ? err.message : 'Could not select agent.'); }
  }
  function saveSkill(skill: AgentSkill) {
    const exists = agent.skills.some(item => item.id === skill.id);
    return update({ ...agent, skills: exists
      ? agent.skills.map(item => item.id === skill.id ? skill : item) : [...agent.skills, skill] });
  }
  async function testServer(server: RemoteServer) {
    discovery.current?.abort();
    const controller = new AbortController();
    discovery.current = controller;
    setTesting(true); setError(''); setHealth([]);
    try {
      const response = await fetch('/api/agent/discover', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({ agent: { id: agent.id, name: agent.name, systemPrompt: '', skills: [],
          servers: [{ ...server, enabled: true }] } }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error ?? 'MCP discovery failed.');
      if (!controller.signal.aborted && discovery.current === controller) setHealth(data.servers);
    } catch (err) {
      if (!controller.signal.aborted && discovery.current === controller) setError(err instanceof Error ? err.message : 'MCP discovery failed.');
    } finally { if (!controller.signal.aborted && discovery.current === controller) setTesting(false); }
  }
  function saveServer(server: RemoteServer) {
    if (server.enabled) {
      const origin = new URL(server.url).origin;
      if (!configuration?.origins.some(item => item.origin === origin && item.authRefs.includes(server.authRef))) {
        setError('This origin/authentication is not operator-approved. Save it disabled or ask the operator to approve the service.');
        return;
      }
    }
    const exists = agent.servers.some(item => item.id === server.id);
    if (update({ ...agent, servers: exists ? agent.servers.map(item => item.id === server.id ? server : item) : [...agent.servers, server] })) {
      setEditingServer(null);
    }
  }
  return (
    <dialog ref={dialog} onCancel={onClose} aria-labelledby="agent-manager-title"
      className="m-auto h-[90vh] w-[min(1100px,95vw)] max-w-none rounded-2xl border border-brand-line bg-brand-paper p-0 text-brand-grey shadow-card backdrop:bg-black/40">
      <div className="flex h-full flex-col">
        <header className="flex items-center justify-between border-b border-brand-line bg-white px-5 py-4">
          <div><h2 id="agent-manager-title" className="font-display text-xl font-bold">Agent Manager</h2>
            <p className="text-sm">Configure personas, skills, system prompt and real remote MCP connections.</p></div>
          <button type="button" onClick={onClose} className={control} aria-label="Close Agent Manager">Close</button>
        </header>
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-5">
          <p className="mb-4 text-sm">Saved for this signed-in account in this browser/profile only. Backend restarts do not clear it; clearing browser data does. No cross-device sync. Never paste keys or tokens into prompts or URLs.</p>
          <div className="mb-4 flex flex-wrap items-end gap-3">
            <label className="flex-1 text-sm font-semibold">Agent persona
              <select className={input} value={agent.id} onChange={event => select(event.target.value)}>
                {preferences.agents.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
            </label>
            <button type="button" className={control} disabled={preferences.agents.length >= 8} onClick={() => {
              try {
                const created = newAgent();
                onChange({ ...preferences, agents: [...preferences.agents, created], selectedAgent: created.id });
              } catch (err) { setError(err instanceof Error ? err.message : 'Could not add agent.'); }
            }}>Add agent</button>
            <button type="button" className={control} disabled={preferences.agents.length === 1} onClick={() => {
              if (!window.confirm(`Remove agent "${agent.name}" from this browser?`)) return;
              const agents = preferences.agents.filter(item => item.id !== agent.id);
              try { onChange({ ...preferences, agents, selectedAgent: agents[0]!.id }); }
              catch (err) { setError(err instanceof Error ? err.message : 'Could not remove agent.'); }
            }}>Remove agent</button>
          </div>
          <nav aria-label="Agent configuration sections" className="mb-4 flex flex-wrap gap-2">
            {(['skills', 'prompt', 'servers'] as const).map(value => <button type="button" key={value}
              className={`${control} ${tab === value ? 'border-brand-purple font-bold' : ''}`}
              aria-pressed={tab === value} onClick={() => { setTab(value); setError(''); setNotice(''); }}>
              {value === 'skills' ? 'Skills' : value === 'prompt' ? 'System Prompt' : 'MCP Servers'}
            </button>)}
          </nav>
          {error && <p role="alert" className="mb-3 rounded-sm border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</p>}
          {notice && <p role="status" className="mb-3 text-sm">{notice}</p>}

          {tab === 'skills' && <section aria-label="Agent skills" className="space-y-4">
            <p className="text-sm">Manage all skills here. Built-in skills are available for every persona but disabled until you choose them. Enabled skills append instructions to every next SDK turn. They do not install tools or grant shell/file permissions.</p>
            <button type="button" className={control} disabled={agent.skills.length >= 32} onClick={() => setEditingSkill({
              id: crypto.randomUUID(), name: '', description: '', prompt: '', enabled: true,
            })}>Add skill</button>
            <div className="grid gap-3 md:grid-cols-2">
              {skills.map(skill => <article key={skill.id} className="space-y-3 rounded-xl border border-brand-line bg-white p-4">
                <div className="flex items-center justify-between gap-3"><h3 className="font-bold">{skill.name}</h3>
                  <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={skill.enabled}
                    onChange={event => saveSkill({ ...skill, enabled: event.target.checked })} />
                    Enable {skill.name}</label></div>
                {builtinIds.has(skill.id) && <p className="text-xs font-semibold">Built-in</p>}
                <p className="text-sm">{skill.description}</p>
                <div className="flex gap-2"><button type="button" className={control} onClick={() => setEditingSkill(skill)}>Edit {skill.name}</button>
                  {!builtinIds.has(skill.id) && <button type="button" className={control} onClick={() => {
                    if (window.confirm(`Delete skill "${skill.name}"?`)) update({ ...agent, skills: agent.skills.filter(item => item.id !== skill.id) });
                  }}>Delete {skill.name}</button>}</div>
              </article>)}
            </div>
            {editingSkill && <form className="space-y-3 rounded-xl border border-brand-line bg-white p-4" onSubmit={event => {
              event.preventDefault();
              const latest = skills.find(skill => skill.id === editingSkill.id);
              if (saveSkill(latest ? { ...editingSkill, enabled: latest.enabled } : editingSkill)) setEditingSkill(null);
            }}>
              <h3 className="font-bold">Skill editor</h3>
              <label className="block">Skill name<input className={input} required maxLength={80} value={editingSkill.name} onChange={event => setEditingSkill({ ...editingSkill, name: event.target.value })} /></label>
              <label className="block">Description<input className={input} maxLength={300} value={editingSkill.description} onChange={event => setEditingSkill({ ...editingSkill, description: event.target.value })} /></label>
              <label className="block">Skill instructions<textarea className={input} required rows={6} maxLength={20000} value={editingSkill.prompt} onChange={event => setEditingSkill({ ...editingSkill, prompt: event.target.value })} /></label>
              <div className="flex gap-2"><button className={control} type="submit">Save skill</button><button className={control} type="button" onClick={() => setEditingSkill(null)}>Cancel skill edit</button></div>
            </form>}
          </section>}

          {tab === 'prompt' && <form className="space-y-4" onSubmit={event => {
            event.preventDefault(); update({ ...agent, name, systemPrompt: prompt });
          }}>
            <label className="block font-semibold">Persona name<input className={input} required maxLength={80} value={name} onChange={event => setName(event.target.value)} /></label>
            <label className="block font-semibold">System prompt<textarea className={input} rows={12} maxLength={20000} value={prompt} onChange={event => setPrompt(event.target.value)} /></label>
            <p className="text-sm">App safety and grounding rules remain enforced. This prompt supplements them; it cannot enable tools or override runtime permissions.</p>
            <div className="flex flex-wrap gap-2">
              <button type="submit" className={control}>Save system prompt</button>
              <button type="button" className={control} disabled={!configuration?.defaultSystemPrompt} onClick={() => {
                if (configuration?.defaultSystemPrompt) {
                  setPrompt(configuration.defaultSystemPrompt);
                  setNotice('Enterprise analyst instructions loaded into the editor. Save system prompt to apply.');
                }
              }}>Use enterprise analyst prompt</button>
            </div>
          </form>}

          {tab === 'servers' && <section aria-label="Remote MCP servers" className="space-y-4">
            <McpServers disabled={disabledMcpServers} onChange={onDisabledMcpServersChange}
              pending={pending} fabricTokenRequired={configuration?.demo !== true && configuration?.fabricTokenRequired === true} />
            <p className="text-sm">Connect real HTTPS Streamable HTTP or legacy SSE MCP services. No demo servers are added here. Stdio/shell is operator-only. Discovery lists tools without calling them; enabled servers are used on the next SDK turn.</p>
            <p className="text-sm">Approved origins: {configuration?.origins.length ? configuration.origins.map(item => item.origin).join(', ') : 'None yet. Ask the operator to approve the real service origin, tools and credential profile.'}</p>
            <p className="text-sm">Credentials stay on the server. A managed-identity profile is application identity, not your delegated identity. Generic OAuth/M365 consent is not configured by this panel.</p>
            <button type="button" className={control} disabled={agent.servers.length >= 8} onClick={() => {
              setHealth([]); setOriginalServerId(null); setServerTools(''); setEditingServer({ id: '', type: 'http', url: '', enabled: false, authRef: 'public', tools: [] });
            }}>Add MCP server</button>
            {!agent.servers.length && <p>No real MCP servers connected. Add an approved service and its authentication reference. Until grounding is connected, chat is an explicitly ungrounded model response.</p>}
            {agent.servers.map(server => <article key={server.id} className="space-y-2 rounded-xl border border-brand-line bg-white p-4">
              <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-bold">{server.id}</h3>
                <label className="flex items-center gap-2"><input type="checkbox" checked={server.enabled} onChange={event => saveServer({ ...server, enabled: event.target.checked })} />Enable {server.id}</label></div>
              <p className="break-all text-sm">{server.type} · {server.url} · {server.authRef}</p>
              <div className="flex flex-wrap gap-2"><button type="button" className={control} onClick={() => { setOriginalServerId(server.id); setServerTools(server.tools.join(', ')); setEditingServer(server); setHealth([]); }}>Edit {server.id}</button>
                <button type="button" className={control} disabled={testing} onClick={() => testServer(server)}>Test {server.id}</button>
                <button type="button" className={control} onClick={() => {
                  if (window.confirm(`Remove MCP server "${server.id}"?`)) {
                    if (update({ ...agent, servers: agent.servers.filter(item => item.id !== server.id) })) {
                      setHealth([]);
                      if (originalServerId === server.id) { setEditingServer(null); setOriginalServerId(null); }
                    }
                  }
                }}>Remove {server.id}</button></div>
            </article>)}
            {editingServer && <form className="space-y-3 rounded-xl border border-brand-line bg-white p-4" onSubmit={event => {
              event.preventDefault();
              if (!originalServerId && agent.servers.some(item => item.id === editingServer.id)) {
                setError('This server ID already exists. Choose another ID or edit the existing connection.'); return;
              }
              if (originalServerId && !agent.servers.some(item => item.id === originalServerId)) {
                setError('This connection was removed. Add a new connection explicitly.'); return;
              }
              const server = { ...editingServer, tools: serverTools.split(',').map(value => value.trim()).filter(Boolean) };
              try { validateAgent({ ...agent, servers: [server] }); saveServer(server); }
              catch (err) { setError(err instanceof Error ? err.message : 'Invalid MCP definition.'); }
            }}>
              <h3 className="font-bold">MCP connection editor</h3>
              <label className="block">Server ID<input className={input} required pattern="[a-z][a-z0-9_-]{0,33}" maxLength={34}
                disabled={originalServerId !== null} value={editingServer.id} onChange={event => setEditingServer({ ...editingServer, id: event.target.value })} /></label>
              <label className="block">Transport<select className={input} value={editingServer.type} onChange={event => setEditingServer({ ...editingServer, type: event.target.value === 'sse' ? 'sse' : 'http' })}><option value="http">Streamable HTTP</option><option value="sse">Legacy SSE</option></select></label>
              <label className="block">MCP URL<input className={input} type="url" required maxLength={2048} placeholder="https://approved-service.example/mcp" value={editingServer.url} onChange={event => setEditingServer({ ...editingServer, url: event.target.value })} /></label>
              <label className="block">Authentication reference<select className={input} value={editingServer.authRef} onChange={event => setEditingServer({ ...editingServer, authRef: event.target.value })}>
                {(configuration?.authReferences ?? [{ id: 'public', label: 'Public service (no credential)', type: 'none' }]).map(ref => <option key={ref.id} value={ref.id}>{ref.label} · {ref.type}</option>)}
              </select></label>
              <label className="block">Tool allowlist (comma separated; blank uses operator-approved tools)<input className={input}
                value={serverTools} onChange={event => setServerTools(event.target.value)} /></label>
              <label className="flex items-center gap-2"><input type="checkbox" checked={editingServer.enabled} onChange={event => setEditingServer({ ...editingServer, enabled: event.target.checked })} />Enable on next chat</label>
              <div className="flex flex-wrap gap-2"><button type="submit" className={control}>Save MCP server</button>
                <button type="button" className={control} disabled={testing} onClick={() => testServer({
                  ...editingServer, tools: serverTools.split(',').map(value => value.trim()).filter(Boolean),
                })}>Test connection and discover tools</button>
                <button type="button" className={control} onClick={() => setEditingServer(null)}>Cancel MCP edit</button></div>
            </form>}
            {testing && <p role="status">Connecting and discovering tools...</p>}
            {health.map(server => <div key={server.id} className="rounded-sm border border-brand-line bg-white p-4">
              <h3 className="font-bold">{server.id}: {server.error ? 'Connection failed' : 'Connected'}</h3>
              {server.error && <p role="alert" className="text-red-800">{server.error}</p>}
              <ul className="list-disc pl-5">{server.tools.map(tool => <li key={tool.name}>{tool.name}{tool.description ? `: ${tool.description}` : ''}</li>)}</ul>
              {!server.error && !server.tools.length && <p>No allowed tools discovered. Check the operator/user tool allowlists.</p>}
            </div>)}
          </section>}
        </div>
      </div>
    </dialog>
  );
}
