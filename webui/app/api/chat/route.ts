import { chatPayload, mcpHeaders } from '@/lib/chat-request';

export const runtime = 'nodejs';
export const maxDuration = 300;

export async function POST(req: Request): Promise<Response> {
  let body;
  try { body = chatPayload(await req.json()); }
  catch (err) {
    return Response.json({ error: err instanceof Error ? err.message : 'Invalid JSON.' }, { status: 400 });
  }
  let headers: Record<string, string>;
  try { headers = mcpHeaders(req, Boolean(body.fabric_selection)); }
  catch { return Response.json({ error: 'Verified Entra identity and service authentication are required.' }, { status: 401 }); }
  try {
    const upstream = await fetch(`${process.env.MCP_BASE_URL ?? 'http://localhost:8765'}/ask`, {
      method: 'POST', headers, body: JSON.stringify(body),
      signal: AbortSignal.any([req.signal, AbortSignal.timeout(240_000)]), cache: 'no-store',
    });
    const result = await upstream.json();
    if (!upstream.ok || result.error) {
      return Response.json({ error: result.error ?? 'Agent failed.' }, { status: upstream.ok ? 502 : upstream.status });
    }
    return Response.json({ answer: result.answer, method: body.method,
      model: result.model, agentId: result.agentId, grounding: result.grounding,
      fabricDataMode: result.fabricDataMode,
      usage: { toolCalls: result.toolCallCount, totalTokens: result.totalTokens, elapsedSec: result.elapsedSec } });
  } catch {
    return Response.json({ error: 'MCP service unavailable or request cancelled.' }, { status: 502 });
  }
}
