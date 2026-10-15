import { test } from 'node:test';
import assert from 'node:assert/strict';
import { POST as upload } from '../app/api/upload/route';
import { GET as document, DELETE as remove } from '../app/api/documents/[id]/route';
import { POST as chat } from '../app/api/chat/route';
import { POST as stream } from '../app/api/chat/stream/route';
import { GET as registry } from '../app/api/mcp/route';
import { samplePdf } from './pdf-fixture';
import { zipSync, unzipSync } from 'fflate';
import { availableAgentSkills, defaultPreferences } from '../lib/agent-config';
import { SEED_SKILLS } from '../lib/skills-seed';

function requestFile(name: string, content: string, type: string, owner = 'alice'): Request {
  const form = new FormData();
  form.append('file', new File([content], name, { type }));
  return new Request('https://local.example/api/upload', { method: 'POST', body: form,
    headers: { 'x-ms-client-principal-id': owner } });
}

test('real multipart upload -> viewer metadata/content -> owner isolation -> cleanup', async () => {
  const response = await upload(requestFile('example.md', '# Example\n\n<script>alert(1)</script>', 'text/markdown'));
  assert.equal(response.status, 200);
  const data = await response.json();
  assert.ok(data.documentId && data.documentUrl && data.sourceId);
  const params = { params: Promise.resolve({ id: data.documentId }) };
  const metadata = await document(new Request(`https://local.example${data.documentUrl}?metadata=1`,
    { headers: { 'x-ms-client-principal-id': 'alice' } }), params);
  assert.equal((await metadata.json()).sourceId, data.sourceId);
  const other = await document(new Request(`https://local.example${data.documentUrl}`,
    { headers: { 'x-ms-client-principal-id': 'bob' } }), params);
  assert.equal(other.status, 404);
  const content = await document(new Request(`https://local.example${data.documentUrl}`,
    { headers: { 'x-ms-client-principal-id': 'alice' } }), params);
  assert.equal(content.headers.get('x-content-type-options'), 'nosniff');
  assert.match(content.headers.get('content-security-policy')!, /sandbox/);
  assert.equal((await remove(new Request(`https://local.example${data.documentUrl}`,
    { headers: { 'x-ms-client-principal-id': 'alice' } }), params)).status, 200);
  assert.equal((await document(new Request(`https://local.example${data.documentUrl}`,
    { headers: { 'x-ms-client-principal-id': 'alice' } }), params)).status, 404);
});

test('route rejects unsupported upload, anonymous upload and chunked oversize body', async () => {
  assert.equal((await upload(requestFile('bad.html', '<script/>', 'text/html'))).status, 415);
  assert.equal((await upload(new Request('https://local.example/api/upload', { method: 'POST',
    body: 'anonymous' }))).status, 401);
  const request = new Request('https://local.example/api/upload', { method: 'POST',
    body: new Uint8Array(11 * 1024 * 1024),
    headers: { 'x-ms-client-principal-id': 'alice', 'content-type': 'multipart/form-data; boundary=x' } });
  assert.equal((await upload(request)).status, 413);
});

test('real PDF upload extracts both pages and retains original viewer bytes', async () => {
  const form = new FormData();
  form.append('file', new File([new Uint8Array(samplePdf())], 'example.pdf', { type: 'application/pdf' }));
  const response = await upload(new Request('https://local.example/api/upload', {
    method: 'POST', body: form, headers: { 'x-ms-client-principal-id': 'alice' },
  }));
  assert.equal(response.status, 200);
  const data = await response.json();
  assert.match(data.text, /Example PDF page 1[\s\S]*Example PDF page 2/);
  const params = { params: Promise.resolve({ id: data.documentId }) };
  const original = await document(new Request(`https://local.example${data.documentUrl}`,
    { headers: { 'x-ms-client-principal-id': 'alice' } }), params);
  assert.deepEqual(Buffer.from(await original.arrayBuffer()), samplePdf());
  await remove(new Request(`https://local.example${data.documentUrl}`,
    { headers: { 'x-ms-client-principal-id': 'alice' } }), params);
});

test('normalized bounded DOCX upload extracts text without rendering XML or HTML', async () => {
  const encode = (value: string) => new TextEncoder().encode(value);
  const bytes = zipSync({
    '[Content_Types].xml': encode('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'),
    '_rels/.rels': encode('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'),
    'word/document.xml': encode('<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Example DOCX text</w:t></w:r></w:p></w:body></w:document>'),
  });
  const form = new FormData();
  form.append('file', new File([bytes], 'example.docx', {
    type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  }));
  const response = await upload(new Request('https://local.example/api/upload', {
    method: 'POST', body: form, headers: { 'x-ms-client-principal-id': 'alice' },
  }));
  assert.equal(response.status, 200);
  const data = await response.json();
  assert.match(data.text, /Example DOCX text/);
  const params = { params: Promise.resolve({ id: data.documentId }) };
  const metadata = await document(new Request(`https://local.example${data.documentUrl}?metadata=1`,
    { headers: { 'x-ms-client-principal-id': 'alice' } }), params);
  assert.equal((await metadata.json()).mime, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document');
  const download = await document(new Request(`https://local.example${data.documentUrl}`,
    { headers: { 'x-ms-client-principal-id': 'alice' } }), params);
  const normalized = unzipSync(new Uint8Array(await download.arrayBuffer()));
  assert.match(Buffer.from(normalized['word/document.xml']!).toString(), /Example DOCX text/);
  await remove(new Request(`https://local.example${data.documentUrl}`,
    { headers: { 'x-ms-client-principal-id': 'alice' } }), params);
});

test('regular and streaming routes share validated payload and delegated token forwarding', async () => {
  const original = globalThis.fetch;
  const originalToken = process.env.MCP_SERVICE_TOKEN;
  process.env.MCP_SERVICE_TOKEN = 'test-service-token';
  const seen: { url: string; body: string; headers: HeadersInit }[] = [];
  globalThis.fetch = async (input, options) => {
    seen.push({ url: String(input), body: String(options?.body), headers: options?.headers ?? {} });
    return String(input).endsWith('/ask/stream')
      ? new Response('event: result\ndata: {"answer":"complete"}\n\n')
      : Response.json({ answer: 'complete', method: 'M4', error: '' });
  };
  try {
    const req = () => new Request('https://local.example/api/chat', { method: 'POST',
      headers: { 'content-type': 'application/json', 'x-fabric-token': 'test-delegated-token',
        'x-ms-client-principal-id': 'alice', 'x-iq-owner': 'browser-spoofed-owner',
        'x-iq-service-token': 'browser-spoofed-secret' },
      body: JSON.stringify({ message: '/stage 2 Question', method: 'M4',
        history: [{ role: 'user', content: 'Context' }],
        ribaStageOverride: 'obsolete', ribaEnabledStages: { obsolete: true } }) });
    assert.equal((await (await chat(req())).json()).answer, 'complete');
    assert.match(await (await stream(req())).text(), /event: result/);
    assert.equal(seen[0]!.body, seen[1]!.body);
    const payload = JSON.parse(seen[0]!.body) as Record<string, unknown>;
    assert.equal(payload.question, '/stage 2 Question');
    assert.ok(Object.keys(payload).every(key => !key.toLowerCase().includes('riba')));
    assert.equal(new Headers(seen[0]!.headers).get('authorization'), 'Bearer test-delegated-token');
    assert.equal(new Headers(seen[1]!.headers).get('authorization'), 'Bearer test-delegated-token');
    assert.equal(new Headers(seen[0]!.headers).get('x-iq-service-token'), 'test-service-token');
    assert.equal(new Headers(seen[1]!.headers).get('x-iq-service-token'), 'test-service-token');
    assert.match(new Headers(seen[0]!.headers).get('x-iq-owner')!, /^[a-f0-9]{64}$/);
    assert.notEqual(new Headers(seen[0]!.headers).get('x-iq-owner'), 'browser-spoofed-owner');
    await registry(new Request('https://local.example/api/mcp', { headers: { 'x-ms-client-principal-id': 'alice' } }));
    assert.equal(new Headers(seen[2]!.headers).get('x-iq-service-token'), 'test-service-token');
    assert.equal(new Headers(seen[2]!.headers).get('authorization'), null);
  } finally {
    globalThis.fetch = original;
    if (originalToken === undefined) delete process.env.MCP_SERVICE_TOKEN;
    else process.env.MCP_SERVICE_TOKEN = originalToken;
  }
});

test('stream interruption becomes an error event, not a successful result', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(new ReadableStream({ start(controller) {
    controller.error(new Error('transport-secret'));
  } }));
  try {
    const response = await stream(new Request('https://local.example/api/chat/stream', {
      method: 'POST', body: JSON.stringify({ message: 'Question' }),
      headers: { 'x-ms-client-principal-id': 'alice' },
    }));
    const text = await response.text();
    assert.match(text, /event: error/);
    assert.doesNotMatch(text, /transport-secret|event: result/);
  } finally { globalThis.fetch = original; }
});

test('regular/SSE routes preserve SDK Work IQ opt-in, model/persona and explicit server opt-outs', async () => {
  const original = globalThis.fetch;
  const bodies: Record<string, unknown>[] = [];
  globalThis.fetch = async (input, options) => {
    bodies.push(JSON.parse(String(options?.body)));
    return String(input).endsWith('/ask/stream')
      ? new Response('event: result\ndata: {"answer":"complete"}\n\n')
      : Response.json({ answer: 'complete', error: '' });
  };
  const agent = {
    id: 'reviewer', name: 'Reviewer', systemPrompt: 'Use evidence.',
    skills: [], servers: [{ id: 'docs', type: 'http', url: 'https://approved.example/mcp',
      enabled: false, authRef: 'public', tools: ['search_docs'] }],
  };
  try {
    for (const enabled of [true, false]) {
      const request = () => new Request('https://local.example/api/chat', {
        method: 'POST', headers: { 'x-ms-client-principal-id': 'test-account' },
        body: JSON.stringify({ message: 'Question', method: 'M4', workIQEnabled: enabled,
          ontologyEnabled: false, disabledMcpServers: ['work-iq'], selectedModel: 'deployment-b', agent }),
      });
      assert.equal((await chat(request())).status, 200);
      assert.match(await (await stream(request())).text(), /event: result/);
      assert.deepEqual(bodies.at(-1), bodies.at(-2));
      assert.deepEqual(bodies.at(-1), {
        question: 'Question', method: 'M4', history: [], images: [], skill: null,
        ontology_enabled: false, workiq_enabled: enabled, disabled_mcp_servers: ['work-iq'],
        selected_model: 'deployment-b', agent, fabric_selection: null,
      });
    }
    for (const handler of [chat, stream]) {
      const response = await handler(new Request('https://local.example/api/chat', {
        method: 'POST', headers: { 'x-ms-client-principal-id': 'test-account' },
        body: JSON.stringify({ message: 'Legacy API question', method: 'M7' }),
      }));
      assert.equal(response.status, 200);
      await response.text();
      assert.equal(bodies.at(-1)?.method, 'M7');
      assert.equal(bodies.at(-1)?.workiq_enabled, false);
    }
  } finally { globalThis.fetch = original; }
});

test('shipped built-in selection and domain prompt survive both proxy request formats unchanged', async () => {
  const original = globalThis.fetch;
  const bodies: Record<string, unknown>[] = [];
  globalThis.fetch = async (input, options) => {
    bodies.push(JSON.parse(String(options?.body)));
    return String(input).endsWith('/ask/stream')
      ? new Response('event: result\ndata: {"answer":"offline"}\n\n')
      : Response.json({ answer: 'offline', error: '' });
  };
  try {
    const persona = defaultPreferences('You are an enterprise consultancy analyst.').agents[0]!;
    const skill = availableAgentSkills(persona)[0]!;
    for (const enabled of [true, false]) {
      const selected = { ...persona, skills: [{ ...skill, enabled }] };
      for (const handler of [chat, stream]) {
        const response = await handler(new Request('https://local.example/api/chat', {
          method: 'POST', headers: { 'x-ms-client-principal-id': 'test-account' },
          body: JSON.stringify({ message: 'Qualify the pursuit.', method: 'M4', agent: selected }),
        }));
        assert.equal(response.status, 200);
        await response.text();
        assert.deepEqual(bodies.at(-1)?.agent, selected);
      }
      assert.deepEqual(bodies.at(-1), bodies.at(-2));
      assert.equal(skill.prompt, SEED_SKILLS[0]!.prompt);
    }
  } finally { globalThis.fetch = original; }
});
