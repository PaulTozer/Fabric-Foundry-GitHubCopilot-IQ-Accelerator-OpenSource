import { validateAgent, type AgentDefinition } from './agent-config';
import { ownerNamespace } from './owner';
import { entraPrincipal } from './entra-principal';
import { validateFabricSelection, type FabricSelection } from './fabric-selection';

export interface ChatPayload {
  question: string;
  method: 'M4' | 'M6' | 'M7';
  history: { role: 'user' | 'assistant'; content: string }[];
  ontology_enabled: boolean;
  workiq_enabled: boolean;
  images: unknown[];
  skill: Record<string, unknown> | null;
  disabled_mcp_servers: string[];
  agent: AgentDefinition | null;
  selected_model: string | null;
  fabric_selection: FabricSelection | null;
}

export function chatPayload(value: unknown): ChatPayload {
  if (!value || typeof value !== 'object') throw new Error('Expected JSON object.');
  const body = value as Record<string, unknown>;
  if (typeof body.message !== 'string' || !body.message.trim() || body.message.length > 250_000) {
    throw new Error('message must be nonempty text of at most 250000 characters.');
  }
  const method = body.method ?? 'M4';
  if (method !== 'M4' && method !== 'M6' && method !== 'M7') throw new Error('Unknown method.');
  const history = body.history ?? [];
  if (!Array.isArray(history) || history.length > 100 || history.some(item =>
    !item || !['user', 'assistant'].includes(item.role) || typeof item.content !== 'string')) {
    throw new Error('Invalid conversation history.');
  }
  const disabled = body.disabledMcpServers ?? [];
  if (!Array.isArray(disabled) || disabled.length > 16 ||
      disabled.some(id => typeof id !== 'string' || !/^[a-z][a-z0-9_-]{0,39}$/.test(id))) {
    throw new Error('Invalid MCP selection.');
  }
  const images = body.images ?? [];
  if (!Array.isArray(images) || images.length > 6 || images.some(image =>
    !image || typeof image.mime !== 'string' ||
    !['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(image.mime) ||
    typeof image.data_url !== 'string' || image.data_url.length > 6_000_000 ||
    !image.data_url.startsWith(`data:${image.mime};base64,`))) {
    throw new Error('Invalid image attachments.');
  }
  if (body.selectedModel !== undefined && body.selectedModel !== null &&
      (typeof body.selectedModel !== 'string' || body.selectedModel.length > 128)) {
    throw new Error('Invalid selected deployment.');
  }
  const agent = body.agent == null ? null : validateAgent(body.agent);
  const selectedModel = typeof body.selectedModel === 'string' ? body.selectedModel : null;
  const fabricSelection = body.fabricSelection == null ? null : validateFabricSelection(body.fabricSelection);
  if (fabricSelection && method === 'M7') throw new Error('Fabric workspace selection applies to M4/M6, not Work IQ.');
  if (method !== 'M4' && (agent || selectedModel)) {
    throw new Error('Agent Manager and selected model apply only to Copilot SDK BYOK.');
  }
  let skill: Record<string, unknown> | null = null;
  if (body.skill) {
    const candidate = body.skill as Record<string, unknown>;
    if (typeof candidate.name !== 'string' || typeof candidate.prompt !== 'string' ||
        candidate.prompt.length > 20_000) throw new Error('Invalid skill.');
    skill = { id: candidate.id, name: candidate.name, prompt: candidate.prompt,
      business_areas: candidate.businessAreas, regions: candidate.regions };
  }
  return {
    question: body.message.trim(), method, history: history.slice(-10),
    ontology_enabled: body.ontologyEnabled !== false, workiq_enabled: body.workIQEnabled === true,
    images, skill,
    disabled_mcp_servers: disabled,
    agent, selected_model: selectedModel,
    fabric_selection: fabricSelection,
  };
}

export function mcpHeaders(req: Request, selectedWorkspace = false): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  headers['X-IQ-Owner'] = ownerNamespace(req);
  const serviceToken = process.env.MCP_SERVICE_TOKEN;
  if (serviceToken) headers['X-IQ-Service-Token'] = serviceToken;
  else if (process.env.NODE_ENV === 'production') {
    throw new Error('MCP service authentication is not configured.');
  }
  const token = req.headers.get('x-fabric-token');
  if (token) headers.Authorization = `Bearer ${token}`;
  if (selectedWorkspace) {
    const principal = entraPrincipal(req);
    headers['X-IQ-Tenant-ID'] = principal.tenant;
    headers['X-IQ-Entra-OID'] = principal.oid;
    const sql = req.headers.get('x-sql-token');
    if (sql) headers['X-IQ-SQL-Token'] = sql;
  }
  return headers;
}
