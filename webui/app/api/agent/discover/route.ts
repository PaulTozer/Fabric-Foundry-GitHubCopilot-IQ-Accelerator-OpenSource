import { managementProxy } from '@/lib/management-proxy';
export const runtime = 'nodejs';
export function POST(req: Request): Promise<Response> { return managementProxy(req, '/discover'); }
