import { createHash } from 'node:crypto';

export function ownerNamespace(req: Request): string {
  let oid = req.headers.get('x-ms-client-principal-id');
  if (!oid && process.env.NODE_ENV !== 'production' && process.env.IQ_LOCAL_AUTH_BYPASS === '1') oid = 'local-user';
  if (!oid) throw new Error('Authentication required.');
  const tenant = process.env.AAD_TENANT_ID ?? 'local';
  return createHash('sha256').update(`${tenant}:${oid}`).digest('hex');
}
