import { mcpHeaders } from '@/lib/chat-request';
export const runtime = 'nodejs';
export async function GET(req: Request): Promise<Response> {
  try {
    const response = await fetch(`${process.env.MCP_BASE_URL ?? 'http://localhost:8765'}/registry`, {
      headers: mcpHeaders(req), signal: AbortSignal.timeout(70_000), cache: 'no-store',
    });
    if (!response.ok) throw new Error('Registry failed.');
    return Response.json(await response.json(), { headers: { 'Cache-Control': 'no-store' } });
  } catch {
    return Response.json({ error: 'MCP registry unavailable; check the internal service.' }, { status: 502 });
  }
}
