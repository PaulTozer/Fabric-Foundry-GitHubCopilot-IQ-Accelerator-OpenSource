import { validateFabricSelection, type FabricSelection } from './fabric-selection';
import { SEED_SKILLS } from './skills-seed';

export interface AgentSkill {
  id: string; name: string; description: string; prompt: string; enabled: boolean;
}
export interface RemoteServer {
  id: string; type: 'http' | 'sse'; url: string; enabled: boolean; authRef: string; tools: string[];
}
export interface AgentDefinition {
  id: string; name: string; systemPrompt: string; skills: AgentSkill[]; servers: RemoteServer[];
}
export interface AgentPreferences {
  agents: AgentDefinition[]; selectedAgent: string; selectedModel: string | null;
  fabricSelection?: FabricSelection | null;
}
export interface OperatorConfiguration {
  origins: { origin: string; authRefs: string[] }[];
  authReferences: { id: string; label: string; type: string }[];
  defaultModel: string; defaultSystemPrompt: string; endpoint: string; authentication: string; fabricConfigured: boolean; demo: boolean;
  ontologyConfigured?: boolean; fabricTokenRequired?: boolean;
}
export interface EnabledModel { id: string; model: string; version: string }
export interface ServerHealth {
  id: string; transport: string; enabled: boolean; tools: { name: string; description?: string }[]; error: string;
}

export function newAgent(): AgentDefinition {
  return { id: crypto.randomUUID(), name: 'Generic Assistant', systemPrompt: '', skills: [], servers: [] };
}
export function defaultPreferences(systemPrompt: string): AgentPreferences {
  if (typeof systemPrompt !== 'string' || !systemPrompt.trim()) {
    throw new Error('Packaged enterprise analyst instructions are unavailable.');
  }
  const agent = validateAgent({ ...newAgent(), name: 'Enterprise Consultancy Analyst', systemPrompt });
  return { agents: [agent], selectedAgent: agent.id, selectedModel: null, fabricSelection: null };
}
/** Catalog availability is not selection. Missing built-ins stay disabled; saved edits win. */
export function availableAgentSkills(agent: AgentDefinition): AgentSkill[] {
  const saved = new Map(agent.skills.map(skill => [skill.id, skill]));
  const builtinIds = new Set(SEED_SKILLS.map(skill => skill.id));
  return [
    ...SEED_SKILLS.map(skill => saved.get(skill.id) ?? {
      id: skill.id, name: skill.name, description: skill.description, prompt: skill.prompt, enabled: false,
    }),
    ...agent.skills.filter(skill => !builtinIds.has(skill.id)),
  ];
}
export function validateAgent(value: unknown): AgentDefinition {
  if (!value || typeof value !== 'object') throw new Error('Invalid agent definition.');
  const agent = value as Record<string, unknown>;
  if (Object.keys(agent).some(key => !['id', 'name', 'systemPrompt', 'skills', 'servers'].includes(key)) ||
      typeof agent.id !== 'string' || !/^[a-zA-Z0-9_-]{1,64}$/.test(agent.id) ||
      typeof agent.name !== 'string' || !agent.name.trim() || agent.name.length > 80 ||
      typeof agent.systemPrompt !== 'string' || agent.systemPrompt.length > 20000 ||
      !Array.isArray(agent.skills) || agent.skills.length > 32 || !Array.isArray(agent.servers) || agent.servers.length > 8) {
    throw new Error('Invalid agent fields or limits.');
  }
  const skillIds = new Set<string>();
  const skills: AgentSkill[] = agent.skills.map((value: unknown) => {
    if (!value || typeof value !== 'object') throw new Error('Invalid skill.');
    const skill = value as Record<string, unknown>;
    if (Object.keys(skill).some(key => !['id', 'name', 'description', 'prompt', 'enabled'].includes(key)) ||
        typeof skill.id !== 'string' || !/^[a-zA-Z0-9_-]{1,64}$/.test(skill.id) || skillIds.has(skill.id) ||
        typeof skill.name !== 'string' || !skill.name.trim() || skill.name.length > 80 ||
        typeof skill.description !== 'string' || skill.description.length > 300 ||
        typeof skill.prompt !== 'string' || !skill.prompt.trim() || skill.prompt.length > 20000 ||
        typeof skill.enabled !== 'boolean') throw new Error('Invalid skill fields or limits.');
    skillIds.add(skill.id);
    return { id: skill.id, name: skill.name, description: skill.description, prompt: skill.prompt, enabled: skill.enabled };
  });
  if (skills.reduce((length, skill) => length + skill.prompt.length, agent.systemPrompt.length) > 40000) {
    throw new Error('Combined agent prompt and skills exceed 40000 characters.');
  }
  const serverIds = new Set<string>();
  const servers: RemoteServer[] = agent.servers.map((value: unknown) => {
    if (!value || typeof value !== 'object') throw new Error('Invalid remote server.');
    const server = value as Record<string, unknown>;
    if (Object.keys(server).some(key => !['id', 'type', 'url', 'enabled', 'authRef', 'tools'].includes(key)) ||
        typeof server.id !== 'string' || !/^[a-z][a-z0-9_-]{0,33}$/.test(server.id) || serverIds.has(server.id) ||
        (server.type !== 'http' && server.type !== 'sse') || typeof server.url !== 'string' || server.url.length > 2048 ||
        typeof server.enabled !== 'boolean' || typeof server.authRef !== 'string' || server.authRef.length > 64 ||
        !Array.isArray(server.tools) || server.tools.length > 64 ||
        server.tools.some(name => typeof name !== 'string' || !/^[A-Za-z_][A-Za-z0-9_.-]{0,79}$/.test(name))) {
      throw new Error('Invalid remote fields; secrets, headers and commands are not accepted.');
    }
    const url = new URL(server.url);
    if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash) {
      throw new Error('MCP URLs require HTTPS without credentials, query or fragment.');
    }
    serverIds.add(server.id);
    return { id: server.id, type: server.type, url: server.url, enabled: server.enabled, authRef: server.authRef, tools: server.tools };
  });
  return { id: agent.id, name: agent.name, systemPrompt: agent.systemPrompt, skills, servers };
}
export function loadAgentPreferences(owner: string, defaultSystemPrompt: string): AgentPreferences {
  const saved = readSavedAgentPreferences(owner);
  if (saved) return saved;
  const defaults = defaultPreferences(defaultSystemPrompt);
  saveAgentPreferences(owner, defaults);
  return defaults;
}
export function readSavedAgentPreferences(owner: string): AgentPreferences | null {
  const raw = window.localStorage.getItem(`enterpriseiq:agents:v1:${owner}`);
  if (!raw) return null;
  const data = JSON.parse(raw) as Record<string, unknown>;
  if (!Array.isArray(data.agents) || !data.agents.length || data.agents.length > 8 ||
      typeof data.selectedAgent !== 'string' ||
      (data.selectedModel !== null && (typeof data.selectedModel !== 'string' || data.selectedModel.length > 128))) {
    throw new Error('Saved Agent Manager configuration is invalid. Clear this browser configuration to start again.');
  }
  const agents = data.agents.map(validateAgent);
  if (!agents.some(agent => agent.id === data.selectedAgent) || new Set(agents.map(agent => agent.id)).size !== agents.length) {
    throw new Error('Saved agent selection is invalid.');
  }
  return { agents, selectedAgent: data.selectedAgent, selectedModel: data.selectedModel as string | null,
    fabricSelection: data.fabricSelection == null ? null : validateFabricSelection(data.fabricSelection) };
}
export function saveAgentPreferences(owner: string, preferences: AgentPreferences): void {
  window.localStorage.setItem(`enterpriseiq:agents:v1:${owner}`, JSON.stringify({
    ...preferences, agents: preferences.agents.map(validateAgent),
    fabricSelection: preferences.fabricSelection == null ? null : validateFabricSelection(preferences.fabricSelection),
  }));
}
