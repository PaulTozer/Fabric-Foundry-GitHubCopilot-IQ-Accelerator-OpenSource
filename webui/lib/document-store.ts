import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { resolve, relative, isAbsolute } from 'node:path';

export interface StoredDocument {
  id: string; owner: string; name: string; mime: string; text: string;
  bytes: Buffer; expires: number; sourceId: string;
}
const host = globalThis as typeof globalThis & { iqDocuments?: Map<string, StoredDocument> };
const documents = host.iqDocuments ??= new Map();
export function documentOwner(req: Request): string {
  const owner = req.headers.get('x-ms-client-principal-id');
  if (owner) return owner;
  if (process.env.NODE_ENV !== 'production' && process.env.IQ_LOCAL_AUTH_BYPASS === '1') return 'local-demo';
  throw new Error('Authentication required.');
}
function prune() {
  for (const [id, doc] of documents) if (doc.expires <= Date.now()) documents.delete(id);
}
export function storeDocument(owner: string, name: string, mime: string, bytes: Buffer, text: string): StoredDocument {
  prune();
  const all = Array.from(documents.values());
  const owned = all.filter(doc => doc.owner === owner);
  if (owned.length >= 16 || owned.reduce((sum, doc) => sum + doc.bytes.length, bytes.length) > 64 * 1024 * 1024 ||
      all.reduce((sum, doc) => sum + doc.bytes.length, bytes.length) > 256 * 1024 * 1024) {
    throw new Error('Document storage limit reached; remove documents or wait for expiry.');
  }
  const id = randomUUID();
  const doc = { id, owner, name, mime, bytes, text, sourceId: id, expires: Date.now() + 30 * 60_000 };
  documents.set(id, doc);
  return doc;
}
export function deleteDocument(id: string, owner: string): boolean {
  prune();
  if (documents.get(id)?.owner !== owner) return false;
  return documents.delete(id);
}
export async function getDocument(id: string, owner: string): Promise<StoredDocument | undefined> {
  prune();
  const uploaded = documents.get(id);
  if (uploaded) return uploaded.owner === owner ? uploaded : undefined;
  if (process.env.IQ_DEMO_MODE === '1' && ['demo-delivery', 'demo-meeting'].includes(id)) {
    const text = id === 'demo-delivery'
      ? '# Example Delivery Guide\n\n## Handover\n\nConfirm scope, owner and evidence before handover.\n\n**Synthetic demonstration document.**'
      : '# Example handover meeting\n\nThe example team agreed to confirm scope and evidence.\n\n**Synthetic demonstration document.**';
    return { id, owner, name: `${id}.md`, mime: 'text/markdown', text, bytes: Buffer.from(text),
      expires: Date.now() + 30 * 60_000, sourceId: id };
  }
  // An operator may publish a trusted source catalog for MCP citation IDs.
  // Local files remain beneath its directory; the browser can supply only ID.
  const catalogPath = process.env.IQ_DOCUMENT_CATALOG;
  if (!catalogPath) return undefined;
  const catalog = JSON.parse(await readFile(catalogPath, 'utf8')) as {
    root: string; documents: { id: string; file: string; mime: string; sourceId?: string }[];
  };
  const entry = catalog.documents.find(item => item.id === id);
  if (!entry) return undefined;
  const root = await import('node:fs/promises').then(fs => fs.realpath(resolve(catalog.root)));
  const path = await import('node:fs/promises').then(fs => fs.realpath(resolve(root, entry.file)));
  const rel = relative(root, path);
  if (rel.startsWith('..') || isAbsolute(rel)) throw new Error('Catalog path escapes document root.');
  const { stat } = await import('node:fs/promises');
  if ((await stat(path)).size > 10 * 1024 * 1024) throw new Error('Catalog document exceeds limit.');
  const bytes = await readFile(path);
  if (!['application/pdf', 'text/plain', 'text/markdown', 'text/csv', 'application/json'].includes(entry.mime)) {
    throw new Error('Unsupported catalog document format.');
  }
  return { id, owner, name: entry.file.split(/[/\\]/).pop()!, mime: entry.mime,
    text: entry.mime === 'application/pdf' ? '' : bytes.toString('utf8'),
    bytes, expires: Date.now() + 30 * 60_000, sourceId: entry.sourceId ?? id };
}
