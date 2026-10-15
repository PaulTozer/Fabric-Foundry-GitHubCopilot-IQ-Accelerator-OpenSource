import { chatPayload, mcpHeaders } from '@/lib/chat-request';

export const runtime = 'nodejs';
export const maxDuration = 300;
const headers = { 'Content-Type': 'text/event-stream; charset=utf-8',
  'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no' };
function error(message: string, status: number): Response {
  return new Response(`event: error\ndata: ${JSON.stringify({ message })}\n\n`, { status, headers });
}

export async function POST(req: Request): Promise<Response> {
  let body;
  try { body = chatPayload(await req.json()); }
  catch (err) { return error(err instanceof Error ? err.message : 'Invalid JSON.', 400); }
  let requestHeaders: Record<string, string>;
  try { requestHeaders = mcpHeaders(req, Boolean(body.fabric_selection)); }
  catch { return error('Verified Entra identity and service authentication are required.', 401); }
  try {
    const upstream = await fetch(`${process.env.MCP_BASE_URL ?? 'http://localhost:8765'}/ask/stream`, {
      method: 'POST', headers: { ...requestHeaders, Accept: 'text/event-stream' },
      body: JSON.stringify(body), cache: 'no-store',
      signal: AbortSignal.any([req.signal, AbortSignal.timeout(240_000)]),
    });
    if (!upstream.ok || !upstream.body) return error('MCP agent service rejected the request.', 502);
    const reader = upstream.body.getReader();
    const stream = new ReadableStream<Uint8Array>({
      async pull(controller) {
        try {
          const { done, value } = await reader.read();
          if (done) { controller.close(); reader.releaseLock(); }
          else controller.enqueue(value);
        } catch {
          controller.enqueue(new TextEncoder().encode(
            'event: error\ndata: {"message":"Upstream stream interrupted; retry the request."}\n\n',
          ));
          controller.close();
          reader.releaseLock();
        }
      },
      async cancel() { await reader.cancel(); reader.releaseLock(); },
    });
    return new Response(stream, { headers });
  } catch { return error('MCP service unavailable or request cancelled.', 502); }
}
