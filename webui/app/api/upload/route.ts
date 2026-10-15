import { validateUpload, MAX_FILE_BYTES, MAX_TEXT_CHARS } from '@/lib/document-policy';
import { documentOwner, storeDocument } from '@/lib/document-store';

export const runtime = 'nodejs';
export const maxDuration = 60;

export async function POST(req: Request): Promise<Response> {
  let owner: string;
  try { owner = documentOwner(req); }
  catch { return Response.json({ error: 'Authentication required.' }, { status: 401 }); }
  // Enforce even chunked multipart bounds before parsing/allocating file buffers.
  const reader = req.body?.getReader();
  if (!reader) return Response.json({ error: 'Upload body required.' }, { status: 400 });
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const item = await reader.read();
      if (item.done) break;
      length += item.value.length;
      if (length > MAX_FILE_BYTES + 64 * 1024) {
        await reader.cancel();
        return Response.json({ error: 'Multipart body exceeds 10 MB upload limit.' }, { status: 413 });
      }
      chunks.push(item.value);
    }
    const form = await new Response(Buffer.concat(chunks), {
      headers: { 'Content-Type': req.headers.get('content-type') ?? '' },
    }).formData();
    const file = form.get('file');
    if (!(file instanceof File) || form.getAll('file').length !== 1) throw new Error('Expected exactly one file.');
    const bytes = Buffer.from(await file.arrayBuffer());
    const { ext, mime, image } = validateUpload(file.name, file.type, file.size, bytes);
    if (image) return Response.json({ kind: 'image', name: file.name, size: file.size, mime,
      ext, text: '', truncated: false, dataUrl: `data:${mime};base64,${bytes.toString('base64')}` });
    let text: string;
    let storedBytes: Buffer = bytes;
    if (ext === 'pdf') {
      text = await (await import('@/lib/pdf-text')).extractPdfText(bytes);
    } else if (ext === 'docx') {
      const { extractDocx } = await import('@/lib/docx-text');
      const document = extractDocx(bytes);
      text = document.text;
      storedBytes = document.bytes;
    } else {
      text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    }
    const truncated = text.length > MAX_TEXT_CHARS;
    text = text.slice(0, MAX_TEXT_CHARS);
    const doc = storeDocument(owner, file.name, mime, storedBytes, text);
    return Response.json({ kind: 'text', name: file.name, mime, size: file.size, ext, text, truncated,
      documentId: doc.id, documentUrl: `/api/documents/${doc.id}`, sourceId: doc.sourceId });
  } catch (err) {
    return Response.json({ error: err instanceof Error ? err.message : 'Document could not be read.' }, { status: 415 });
  } finally { reader.releaseLock(); }
}
