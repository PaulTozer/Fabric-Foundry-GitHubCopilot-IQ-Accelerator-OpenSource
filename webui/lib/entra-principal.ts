import { fabricId } from './fabric-selection';

/** Easy Auth's platform principal ID is not necessarily the Entra object ID. */
export function entraPrincipal(req: Request): { oid: string; tenant: string } {
  const raw = req.headers.get('x-ms-client-principal');
  if (!raw || raw.length > 32000) throw new Error('Verified Entra principal claims are unavailable.');
  let data: unknown;
  try { data = JSON.parse(Buffer.from(raw, 'base64').toString('utf8')); }
  catch { throw new Error('Verified Entra principal claims are unavailable.'); }
  if (!data || typeof data !== 'object' || !('claims' in data) || !Array.isArray(data.claims)) {
    throw new Error('Verified Entra principal claims are unavailable.');
  }
  const claims: unknown[] = data.claims;
  const claim = (names: string[]): string => {
    const values = claims.filter((item): item is { typ: string; val: unknown } =>
      Boolean(item) && typeof item === 'object' && item !== null &&
      'typ' in item && 'val' in item && typeof item.typ === 'string' && names.includes(item.typ),
    ).map(item => item.val);
    if (!values.length || values.some(value => value !== values[0]) || typeof values[0] !== 'string') {
      throw new Error('Verified Entra identity claims are missing or conflicting.');
    }
    return fabricId(values[0]);
  };
  const oid = claim(['oid', 'http://schemas.microsoft.com/identity/claims/objectidentifier']);
  const tenant = claim(['tid', 'http://schemas.microsoft.com/identity/claims/tenantid']);
  if (tenant !== process.env.AAD_TENANT_ID?.toLowerCase()) throw new Error('Entra principal tenant does not match this application.');
  return { oid, tenant };
}
