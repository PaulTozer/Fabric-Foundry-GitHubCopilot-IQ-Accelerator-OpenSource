import { test } from 'node:test';
import assert from 'node:assert/strict';
import { entraPrincipal } from '../lib/entra-principal';
import { chatPayload, mcpHeaders } from '../lib/chat-request';
import { loadAgentPreferences, saveAgentPreferences, type AgentPreferences } from '../lib/agent-config';
import { validateFabricSelection } from '../lib/fabric-selection';
import { assertPreferenceOwner, getWorkspaceAccessToken } from '../lib/fabric-token';
import { POST as chat } from '../app/api/chat/route';
import { POST as stream } from '../app/api/chat/stream/route';
import { POST as workspace } from '../app/api/fabric/workspace/route';

const tenant = '11111111-1111-4111-8111-111111111111';
const oid = '22222222-2222-4222-8222-222222222222';
const selection = { workspaceId: '33333333-3333-4333-8333-333333333333',
  lakehouseId: '44444444-4444-4444-8444-444444444444', ontologyId: null };
const principal = (claims = [{ typ: 'oid', val: oid }, { typ: 'tid', val: tenant }]) =>
  Buffer.from(JSON.stringify({ claims })).toString('base64');
function request(body: object, verified = true) {
  return new Request('https://local.example/api/chat', { method: 'POST',
    headers: { 'content-type': 'application/json', 'x-ms-client-principal-id': 'platform-owner-not-entra-oid',
      ...(verified ? { 'x-ms-client-principal': principal() } : {}),
      'x-fabric-token': 'fixture-fabric-token', 'x-sql-token': 'fixture-sql-token',
      'x-iq-entra-oid': 'browser-spoofed', 'x-iq-tenant-id': 'browser-spoofed',
      'x-iq-sql-token': 'browser-spoofed' },
    body: JSON.stringify(body) });
}

test('selection validates UUID-only fields, explicit modes and legacy no-selection DTO', () => {
  assert.deepEqual(validateFabricSelection(selection), selection);
  assert.equal(chatPayload({ message: 'Question' }).fabric_selection, null);
  assert.deepEqual(chatPayload({ message: 'Question', fabricSelection: selection }).fabric_selection, selection);
  for (const value of [{ ...selection, url: 'https://untrusted.example' },
    { ...selection, workspaceId: 'invalid' }, { ...selection, lakehouseId: null }]) {
    assert.throws(() => validateFabricSelection(value));
  }
  assert.throws(() => chatPayload({ message: 'Question', method: 'M7', fabricSelection: selection }), /Work IQ/);
});

test('verified Entra claims are distinct from platform ID and cannot be spoofed by browser forwarding headers', () => {
  const previous = process.env.AAD_TENANT_ID;
  process.env.AAD_TENANT_ID = tenant;
  try {
    assert.deepEqual(entraPrincipal(request({})), { oid, tenant });
    const headers = mcpHeaders(request({}), true);
    assert.equal(headers['X-IQ-Entra-OID'], oid);
    assert.equal(headers['X-IQ-Tenant-ID'], tenant);
    assert.equal(headers['X-IQ-SQL-Token'], 'fixture-sql-token');
    assert.match(headers['X-IQ-Owner']!, /^[a-f0-9]{64}$/);
    assert.throws(() => entraPrincipal(request({}, false)), /claims/);
    assert.throws(() => entraPrincipal(new Request('https://local.example', {
      headers: { 'x-ms-client-principal': principal([{ typ: 'oid', val: oid },
        { typ: 'oid', val: tenant }, { typ: 'tid', val: tenant }]) },
    })), /conflicting/);
    assert.equal(mcpHeaders(request({}))['X-IQ-SQL-Token'], undefined);
  } finally {
    if (previous === undefined) delete process.env.AAD_TENANT_ID;
    else process.env.AAD_TENANT_ID = previous;
  }
});

test('account-partitioned preference migration, save/reload and clear preserve other preferences', () => {
  const storage = new Map<string, string>();
  const original = Object.getOwnPropertyDescriptor(globalThis, 'window');
  Object.defineProperty(globalThis, 'window', { configurable: true, value: { localStorage: {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
  } } });
  const preferences: AgentPreferences = { agents: [{ id: 'generic', name: 'Generic', systemPrompt: '',
    skills: [], servers: [] }], selectedAgent: 'generic', selectedModel: 'retained-model' };
  try {
    storage.set('enterpriseiq:agents:v1:alice', JSON.stringify(preferences));
    assert.equal(loadAgentPreferences('alice', 'Enterprise instructions.').fabricSelection, null);
    saveAgentPreferences('alice', { ...preferences, fabricSelection: selection });
    assert.deepEqual(loadAgentPreferences('alice', 'Enterprise instructions.').fabricSelection, selection);
    assert.equal(loadAgentPreferences('bob', 'Enterprise instructions.').fabricSelection, null);
    saveAgentPreferences('alice', { ...loadAgentPreferences('alice', 'Enterprise instructions.'), fabricSelection: null });
    assert.equal(loadAgentPreferences('alice', 'Enterprise instructions.').selectedModel, 'retained-model');
    assert.equal(loadAgentPreferences('alice', 'Enterprise instructions.').fabricSelection, null);
    assert.doesNotMatch(storage.get('enterpriseiq:agents:v1:alice')!, /token|endpoint/);
  } finally {
    if (original) Object.defineProperty(globalThis, 'window', original);
    else Reflect.deleteProperty(globalThis, 'window');
  }
});

test('regular/SSE/workspace proxies share authenticated selection; failures cannot silently default', async () => {
  const original = globalThis.fetch;
  const previousTenant = process.env.AAD_TENANT_ID;
  process.env.AAD_TENANT_ID = tenant;
  const seen: { body: string; headers: Headers }[] = [];
  globalThis.fetch = async (url, options) => {
    seen.push({ body: String(options?.body), headers: new Headers(options?.headers) });
    return String(url).endsWith('/ask/stream')
      ? new Response('event: result\ndata: {"answer":"fixture","fabricDataMode":"existing"}\n\n')
      : Response.json({ answer: 'fixture', fabricDataMode: 'existing', metadataValidated: true, error: '' });
  };
  try {
    const body = { message: 'Question', fabricSelection: selection };
    assert.equal((await (await chat(request(body))).json()).fabricDataMode, 'existing');
    assert.match(await (await stream(request(body))).text(), /event: result/);
    assert.equal(seen[0]!.body, seen[1]!.body);
    assert.deepEqual(JSON.parse(seen[0]!.body).fabric_selection, selection);
    assert.equal(seen[0]!.headers.get('x-iq-entra-oid'), oid);
    assert.equal((await workspace(request(selection))).status, 200);
    assert.deepEqual(JSON.parse(seen[2]!.body), selection);
    const count = seen.length;
    assert.equal((await chat(request(body, false))).status, 401);
    assert.equal((await stream(request(body, false))).status, 401);
    assert.equal(seen.length, count);
  } finally {
    globalThis.fetch = original;
    if (previousTenant === undefined) delete process.env.AAD_TENANT_ID;
    else process.env.AAD_TENANT_ID = previousTenant;
  }
});

test('current account must match the loaded preference owner before acquiring workspace credentials or saving', async () => {
  const original = globalThis.fetch;
  let owner = 'alice';
  globalThis.fetch = async () => Response.json({ configOwner: owner });
  try {
    assert.equal((await assertPreferenceOwner('alice')).configOwner, 'alice');
    owner = 'bob';
    await assert.rejects(assertPreferenceOwner('alice'), /account changed/);
    await assert.rejects(getWorkspaceAccessToken('fabric', 'alice'), /account changed/);
    await assert.rejects(getWorkspaceAccessToken('sql', 'alice'), /account changed/);
    assert.equal((await assertPreferenceOwner('bob')).configOwner, 'bob');
  } finally {
    globalThis.fetch = original;
  }
});
