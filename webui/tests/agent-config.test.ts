import { test } from 'node:test';
import assert from 'node:assert/strict';
import { availableAgentSkills, defaultPreferences, loadAgentPreferences, saveAgentPreferences, validateAgent } from '../lib/agent-config';
import { SEED_SKILLS } from '../lib/skills-seed';
import { chatPayload } from '../lib/chat-request';
import { ownerNamespace } from '../lib/owner';

const agent = { id: 'generic', name: 'Generic Assistant', systemPrompt: 'Be concise.',
  skills: [{ id: 'brief', name: 'Brief', description: '', prompt: 'Use evidence.', enabled: true }],
  servers: [] };

test('selected agent, skills and deployment preserve one validated regular/SSE payload', () => {
  const payload = chatPayload({ message: 'Question', method: 'M4', agent, selectedModel: 'deployed-id' });
  assert.deepEqual(payload.agent, agent);
  assert.equal(payload.selected_model, 'deployed-id');
  assert.throws(() => chatPayload({ message: 'Question', method: 'M6', agent }), /only/);
  assert.throws(() => chatPayload({ message: 'Question', selectedModel: {} }), /deployment/);
});

test('manager definitions never accept shell/stdio, raw secrets, credential URLs or excess prompts', () => {
  assert.throws(() => validateAgent({ ...agent, systemPrompt: 'x'.repeat(20001) }));
  for (const extra of [{ type: 'stdio', command: 'cmd.exe' }, { headers: { Authorization: 'secret' } },
    { url: 'https://username:secret@approved.example/mcp' }]) {
    assert.throws(() => validateAgent({ ...agent, servers: [{
      id: 'approved', type: 'http', url: 'https://approved.example/mcp', authRef: 'public', enabled: true, tools: [], ...extra,
    }] }));
  }
});

test('storage namespace depends on authenticated account, not user-supplied owner', () => {
  const req = (id: string) => new Request('https://app.example/api/me', {
    headers: { 'x-ms-client-principal-id': id, 'x-iq-owner': 'spoofed' },
  });
  assert.match(ownerNamespace(req('alice')), /^[a-f0-9]{64}$/);
  assert.notEqual(ownerNamespace(req('alice')), ownerNamespace(req('bob')));
  assert.equal(ownerNamespace(req('alice')), ownerNamespace(req('alice')));
  assert.throws(() => ownerNamespace(new Request('https://app.example')), /Authentication/);
});

test('shipped catalog is available for fresh and existing personas without implicitly enabling skills', () => {
  const defaults = defaultPreferences('You are an enterprise consultancy analyst.');
  const persona = defaults.agents[0]!;
  assert.equal(persona.name, 'Enterprise Consultancy Analyst');
  assert.equal(persona.systemPrompt, 'You are an enterprise consultancy analyst.');
  assert.deepEqual(persona.servers, []);
  assert.deepEqual(persona.skills, []);
  assert.deepEqual(availableAgentSkills(persona).map(skill => skill.id), SEED_SKILLS.map(skill => skill.id));
  assert.equal(availableAgentSkills(persona).filter(skill => skill.enabled).length, 0);
  assert.deepEqual(availableAgentSkills(agent).at(-1), agent.skills[0]);
  assert.throws(() => defaultPreferences(''), /instructions/);
});

test('catalog preserves explicit built-in edits/disabled choices and validates normal SDK payloads', () => {
  const builtin = SEED_SKILLS[0]!;
  const edited = { id: builtin.id, name: builtin.name, description: builtin.description,
    prompt: 'Use our explicit review checklist.', enabled: false };
  const persona = { ...agent, skills: [edited, ...agent.skills] };
  const skills = availableAgentSkills(persona);
  assert.deepEqual(skills[0], edited);
  assert.deepEqual(persona.skills, [edited, ...agent.skills]);
  const selected = { ...persona, skills: [{ ...skills[1]!, enabled: true }, ...persona.skills] };
  assert.deepEqual(chatPayload({ message: 'Question', agent: selected }).agent, selected);
});

test('account-scoped defaults persist while legacy/custom/empty settings remain authoritative', () => {
  const store = new Map<string, string>();
  const original = Object.getOwnPropertyDescriptor(globalThis, 'window');
  Object.defineProperty(globalThis, 'window', { configurable: true, value: {
    localStorage: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => store.set(key, value),
    },
  } });
  try {
    store.set('enterpriseiq:skills:v1', JSON.stringify([{ ...SEED_SKILLS[0], prompt: 'Another account private skill' }]));
    const fresh = loadAgentPreferences('alice', 'Enterprise domain instructions.');
    assert.deepEqual(loadAgentPreferences('alice', 'New shipped instructions.'), fresh);
    assert.ok(!JSON.stringify(fresh).includes('Another account'));
    const explicit = { agents: [{ ...agent, systemPrompt: '', skills: [] }],
      selectedAgent: agent.id, selectedModel: 'deployment-b',
      fabricSelection: { workspaceId: '33333333-3333-4333-8333-333333333333',
        lakehouseId: '44444444-4444-4444-8444-444444444444', ontologyId: null } };
    saveAgentPreferences('alice', explicit);
    const before = store.get('enterpriseiq:agents:v1:alice');
    assert.deepEqual(loadAgentPreferences('alice', 'New domain instructions.'), explicit);
    assert.equal(store.get('enterpriseiq:agents:v1:alice'), before);
    assert.equal(availableAgentSkills(explicit.agents[0]!).length, 6);
    assert.equal(loadAgentPreferences('bob', 'Enterprise domain instructions.').agents[0]!.name,
      'Enterprise Consultancy Analyst');
    assert.equal(store.get('enterpriseiq:agents:v1:alice'), before);
    store.set('enterpriseiq:agents:v1:invalid', '{"agents":[]}');
    assert.throws(() => loadAgentPreferences('invalid', 'Domain'), /invalid/);
  } finally {
    if (original) Object.defineProperty(globalThis, 'window', original);
    else Reflect.deleteProperty(globalThis, 'window');
  }
});
