import { documentOwner, getDocument, deleteDocument } from '@/lib/document-store';
export const runtime = 'nodejs';
type Context = { params: Promise<{ id: string }> };
export async function GET(req: Request, context: Context): Promise<Response> {
  try {
    const owner = documentOwner(req);
    const { id } = await context.params;
    if (!/^[a-zA-Z0-9_-]{1,80}$/.test(id)) return Response.json({ error: 'Invalid document ID.' }, { status: 400 });
    const doc = await getDocument(id, owner);
    if (!doc) return Response.json({ error: 'Document not found or expired.' }, { status: 404 });
    const url = new URL(req.url);
    if (url.searchParams.get('metadata') === '1') {
      return Response.json({ name: doc.name, mime: doc.mime, sourceId: doc.sourceId,
        text: doc.text, url: `/api/documents/${id}`, expires: doc.expires },
      { headers: { 'Cache-Control': 'private, no-store' } });
    }
    return new Response(new Uint8Array(doc.bytes), { headers: {
      'Content-Type': doc.mime, 'Content-Disposition': `inline; filename*=UTF-8''${encodeURIComponent(doc.name)}`,
      'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
      'Content-Security-Policy': "default-src 'none'; sandbox",
    } });
  } catch {
    return Response.json({ error: 'Document access failed; check authentication and source catalog.' }, { status: 403 });
  }
}
export async function DELETE(req: Request, context: Context): Promise<Response> {
  try {
    const { id } = await context.params;
    return Response.json({ deleted: deleteDocument(id, documentOwner(req)) });
  } catch { return Response.json({ error: 'Authentication required.' }, { status: 401 }); }
}
