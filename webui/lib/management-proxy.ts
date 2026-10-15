import { mcpHeaders } from './chat-request';
import { ownerNamespace } from './owner';

export async function managementProxy(req: Request, path: '/configuration' | '/models' | '/discover' | '/fabric/workspace'): Promise<Response> {
  try { ownerNamespace(req); }
  catch { return Response.json({ error: 'Authentication required.' }, { status: 401 }); }
  let body: string | undefined;
  if (req.method === 'POST') {
    body = await req.text();
    if (Buffer.byteLength(body, 'utf8') > 100000) return Response.json({ error: 'Agent definition exceeds the safe limit.' }, { status: 413 });
  }
  let headers: Record<string, string>;
  try { headers = mcpHeaders(req, path === '/fabric/workspace'); }
  catch { return Response.json({ error: 'Verified Entra identity and service authentication are required.' }, { status: 401 }); }
  try {
    const suffix = path === '/models' && new URL(req.url).searchParams.get('refresh') === '1' ? '?refresh=1' : '';
    const response = await fetch(`${process.env.MCP_BASE_URL ?? 'http://127.0.0.1:8765'}${path}${suffix}`, {
      method: req.method, headers, body, cache: 'no-store',
      signal: AbortSignal.any([req.signal, AbortSignal.timeout(50000)]),
    });
    return Response.json(await response.json(), {
      status: response.status, headers: { 'Cache-Control': 'no-store' },
    });
  } catch {
    return Response.json({ error: 'Management service unavailable. Check the internal MCP service and authentication.' }, { status: 502 });
  }
}
