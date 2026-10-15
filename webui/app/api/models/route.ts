import { managementProxy } from '@/lib/management-proxy';
export const runtime = 'nodejs';
export function GET(req: Request): Promise<Response> { return managementProxy(req, '/models'); }
