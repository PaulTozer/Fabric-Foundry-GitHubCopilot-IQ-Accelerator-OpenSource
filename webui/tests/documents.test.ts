import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validateUpload, documentReference, safeMarkdownUrl } from '../lib/document-policy';
import { storeDocument, getDocument, deleteDocument } from '../lib/document-store';
import { checkDocxBounds } from '../lib/docx-bounds';
import { chatPayload } from '../lib/chat-request';

test('upload extension, MIME, traversal, binary and size policy', () => {
  const bytes = Buffer.from('example text');
  assert.equal(validateUpload('example.md', 'text/plain', bytes.length, bytes).mime, 'text/markdown');
  for (const name of ['../secret.txt', 'C:\\secret.txt', 'example.html', 'bad.svg', 'run.exe']) {
    assert.throws(() => validateUpload(name, 'text/plain', 12, bytes));
  }
  assert.throws(() => validateUpload('example.pdf', 'text/plain', 12, bytes));
  assert.throws(() => validateUpload('example.pdf', 'application/pdf', 12, bytes));
  assert.throws(() => validateUpload('example.png', 'image/png', 12, bytes));
  assert.throws(() => validateUpload('example.txt', 'text/plain', 11 * 1024 * 1024, bytes));
  assert.throws(() => validateUpload('example.txt', 'text/plain', 1, Buffer.from([0])));
  assert.throws(() => checkDocxBounds(Buffer.from('not a zip')));
});

test('viewer citation URL preserves opaque source and page but blocks hostile URLs', () => {
  assert.deepEqual(documentReference('/api/documents/demo-delivery?page=2'), { id: 'demo-delivery', page: 2 });
  for (const url of ['/api/documents/../../etc/passwd', '//evil.example/file', 'javascript:alert(1)',
    'file:///etc/passwd', '/api/documents/x?url=http://127.0.0.1', '/api/documents/x?page=0']) {
    assert.equal(documentReference(url), null);
    assert.equal(safeMarkdownUrl(url), '');
  }
  assert.equal(safeMarkdownUrl('https://example.com/report'), 'https://example.com/report');
});

test('document ownership, delete and TTL isolate users', async () => {
  const doc = storeDocument('owner-a', 'sample.txt', 'text/plain', Buffer.from('hello'), 'hello');
  assert.equal((await getDocument(doc.id, 'owner-a'))?.text, 'hello');
  assert.equal(await getDocument(doc.id, 'owner-b'), undefined);
  assert.equal(deleteDocument(doc.id, 'owner-b'), false);
  doc.expires = Date.now() - 1;
  assert.equal(await getDocument(doc.id, 'owner-a'), undefined);
});

test('chat supports all three methods and never accepts BYOK or MCP endpoints from UI', () => {
  const payload = chatPayload({ message: 'Question', method: 'M4', provider: { apiKey: 'do-not-forward' },
    mcpServers: [{ command: 'untrusted' }], disabledMcpServers: ['work-iq'] });
  assert.equal(JSON.stringify(payload).includes('do-not-forward'), false);
  assert.equal(JSON.stringify(payload).includes('untrusted'), false);
  assert.deepEqual(payload.disabled_mcp_servers, ['work-iq']);
  assert.throws(() => chatPayload({ message: {}, method: 'M4' }));
  assert.throws(() => chatPayload({ message: 'x', method: 'M1' }));
  assert.throws(() => chatPayload({ message: 'x', disabledMcpServers: ['../shell'] }));
  assert.equal(chatPayload({ message: 'x', method: 'M6' }).method, 'M6');
  assert.equal(chatPayload({ message: 'x', method: 'M7' }).method, 'M7');
});
