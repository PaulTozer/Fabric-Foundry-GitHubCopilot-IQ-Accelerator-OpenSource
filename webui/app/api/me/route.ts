import type { NextRequest } from 'next/server';
import { ownerNamespace } from '@/lib/owner';
import { entraPrincipal } from '@/lib/entra-principal';

// Returns the signed-in user's UPN (and a few useful claims) by reading
// Easy Auth's injected request headers, plus the AAD client/tenant IDs
// MSAL.js needs to bootstrap in the browser.
//
// We expose the AAD config at runtime (via this route) rather than as
// NEXT_PUBLIC_* build-args because:
//   * Next.js inlines NEXT_PUBLIC_* into the static bundle at `next build`
//     time, so any propagation hiccup through ACR Tasks remote builds
//     (azd buildArgs → docker --build-arg → ARG/ENV) ships an empty value
//     to the client and MSAL throws "must be set" at runtime.
//   * Runtime env vars are just regular Container App env vars — set
//     directly by Bicep, no build coupling, changeable without rebuild.
//
// /.auth/me is unavailable because the Easy Auth token store requires
// Azure Blob SAS (disabled in this environment), but the principal
// headers below are always injected.
//
// The browser uses `upn` as a `loginHint` so MSAL ssoSilent can mint a
// Fabric-audience access token without a redirect, and uses
// `aadClientId` + `aadTenantId` to construct the MSAL configuration.

export const runtime = 'nodejs';

export async function GET(req: NextRequest): Promise<Response> {
  let configOwner: string;
  try { configOwner = ownerNamespace(req); }
  catch { return Response.json({ error: 'Authentication required.' }, { status: 401 }); }
  const upn  = req.headers.get('x-ms-client-principal-name') ?? '';
  let oid = '';
  try { oid = entraPrincipal(req).oid; }
  catch { /* Legacy no-selection chat does not require delegated identity claims. */ }
  const aadClientId = process.env.AAD_CLIENT_ID ?? '';
  const aadTenantId = process.env.AAD_TENANT_ID ?? '';
  return new Response(
    JSON.stringify({ upn, oid, aadClientId, aadTenantId, configOwner, demo: process.env.IQ_DEMO_MODE === '1' }),
    {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-store',
      },
    },
  );
}
