'use client';

// Browser-side acquisition of a Microsoft Fabric access token using MSAL.js.
//
// Why this exists: Easy Auth on Container Apps would normally inject
// `x-ms-token-aad-access-token` for us, but its token store requires Azure
// Blob Storage (SAS-only), which is disabled in this environment. So we use
// MSAL.js to silently acquire a Fabric-audience token in the browser
// (the user is already SSO'd via Easy Auth — same Entra app, same tenant —
// so ssoSilent is non-interactive in practice) and forward that token
// alongside each request to /api/chat/stream as `X-Fabric-Token`.
//
// AAD client/tenant IDs come from /api/me at runtime (Container App env
// vars) rather than NEXT_PUBLIC_* build args. The build-arg path through
// ACR Tasks remote builds is fragile — a single empty propagation ships
// an unusable client bundle and the user only finds out when MSAL throws.
// Runtime fetching keeps the bundle config-free and lets ops rotate the
// app registration without a rebuild.

import {
  PublicClientApplication,
  type AccountInfo,
  type Configuration,
  InteractionRequiredAuthError,
  BrowserAuthError,
} from '@azure/msal-browser';

const FABRIC_SCOPES = [
  'https://api.fabric.microsoft.com/Workspace.Read.All',
  'https://api.fabric.microsoft.com/Item.ReadWrite.All',
  'https://api.fabric.microsoft.com/Item.Execute.All',
];

type AuthMe = {
  configOwner?: string;
  upn?: string;
  oid?: string;
  aadClientId?: string;
  aadTenantId?: string;
};

let _instance: PublicClientApplication | null = null;
let _initPromise: Promise<PublicClientApplication> | null = null;
let _cachedToken: { token: string; expiresOn: number } | null = null;
let _mePromise: Promise<AuthMe> | null = null;

/**
 * Fetch and cache the /api/me payload (UPN + AAD config). We dedupe
 * in-flight calls with a module-level promise so concurrent acquires
 * during page load share a single request.
 */
function _getMe(): Promise<AuthMe> {
  if (_mePromise) return _mePromise;
  _mePromise = (async () => {
    try {
      const r = await fetch('/api/me', {
        credentials: 'include',
        cache: 'no-store',
      });
      if (!r.ok) return {};
      return (await r.json()) as AuthMe;
    } catch {
      return {};
    }
  })();
  return _mePromise;
}

async function _config(): Promise<Configuration> {
  const me = await _getMe();
  const clientId = me.aadClientId ?? '';
  const tenantId = me.aadTenantId ?? '';
  if (!clientId || !tenantId) {
    throw new Error(
      'AAD_CLIENT_ID and AAD_TENANT_ID are not configured on the webui '
      + 'Container App. Redeploy infra (`azd provision` then `azd deploy webui`) '
      + 'so /api/me returns the Entra client/tenant IDs needed to bootstrap MSAL.',
    );
  }
  return {
    auth: {
      clientId,
      authority: `https://login.microsoftonline.com/${tenantId}`,
      redirectUri: typeof window !== 'undefined' ? window.location.origin + '/' : '/',
    },
    cache: {
      cacheLocation: 'sessionStorage', // cleared when tab closes; Easy Auth re-SSOs anyway
      storeAuthStateInCookie: false,
    },
    system: {
      // Default logger off; flip on for diagnosis if needed.
      loggerOptions: { logLevel: 0, piiLoggingEnabled: false },
    },
  };
}

async function _getInstance(): Promise<PublicClientApplication> {
  if (_instance) return _instance;
  if (_initPromise) return _initPromise;
  _initPromise = (async () => {
    const pca = new PublicClientApplication(await _config());
    await pca.initialize();
    // If we are returning from a redirect-based interactive flow, finish it.
    await pca.handleRedirectPromise();
    _instance = pca;
    return pca;
  })();
  return _initPromise;
}

/**
 * Read the signed-in Easy Auth user's UPN. We bounce through our own
 * `/api/me` route because Easy Auth's `/.auth/me` endpoint is only
 * available when the token store is on (and our environment can't host
 * the SAS-backed token store). `x-ms-client-principal-name` is always
 * injected, though, so the route just echoes that header back.
 *
 * Used as a `loginHint` so MSAL's ssoSilent can find the right Entra
 * session without prompting.
 */
async function _easyAuthUpn(): Promise<string | null> {
  const me = await _getMe();
  return me.upn ?? null;
}

function _pickAccount(pca: PublicClientApplication, hint: string | null): AccountInfo | null {
  const accounts = pca.getAllAccounts();
  if (accounts.length === 0) return null;
  if (hint) {
    const lower = hint.toLowerCase();
    const match = accounts.find(
      (a) => a.username?.toLowerCase() === lower,
    );
    if (match) return match;
  }
  return accounts[0]!;
}

/**
 * Acquire a Fabric token. Strategy:
 *   1. If we already have an MSAL account cached, try acquireTokenSilent.
 *   2. Otherwise, attempt ssoSilent with the Easy Auth UPN as loginHint
 *      (works only when third-party cookies for login.microsoftonline.com
 *      are allowed — modern browsers in default mode often block this).
 *   3. If silent paths fail, fall back to acquireTokenPopup.
 *      Popup keeps chat state intact (vs. acquireTokenRedirect which
 *      navigates the page away and dies inside the page's try/catch).
 *      Must be called inside a user gesture (e.g. click handler) to avoid
 *      the popup blocker — getFabricAccessToken() is invoked from the
 *      Send-button click path so this is fine.
 */
async function _acquire(forceRefresh = false): Promise<string> {
  const pca = await _getInstance();
  const upn = await _easyAuthUpn();
  let account = _pickAccount(pca, upn);

  if (!account) {
    try {

      console.info('[fabric-token] no cached account; attempting ssoSilent', { upn });
      const result = await pca.ssoSilent({
        scopes: FABRIC_SCOPES,
        loginHint: upn ?? undefined,
      });
      account = result.account ?? _pickAccount(pca, upn);
      if (result.accessToken) {

        console.info('[fabric-token] ssoSilent succeeded');
        return result.accessToken;
      }
    } catch (err) {
      // Anything thrown here (InteractionRequired, BrowserAuth, network)
      // means we need the user. Don't swallow — fall through to popup.

      console.info('[fabric-token] ssoSilent failed, will try popup',
        err instanceof Error ? err.name : err);
    }
  }

  if (account) {
    try {
      const result = await pca.acquireTokenSilent({
        scopes: FABRIC_SCOPES,
        account,
        forceRefresh,
      });

      console.info('[fabric-token] acquireTokenSilent succeeded');
      return result.accessToken;
    } catch (err) {
      if (!(err instanceof InteractionRequiredAuthError)
          && !(err instanceof BrowserAuthError)) {
        throw err;
      }
      // Token expired / consent revoked — drop through to popup.

      console.info('[fabric-token] silent failed, falling back to popup');
    }
  }

  // Interactive popup. Tied to the Send-button user gesture so the popup
  // blocker shouldn't fire. If it does, MSAL throws a BrowserAuthError
  // that the caller can surface to the user.
  try {
    const result = await pca.acquireTokenPopup({
      scopes: FABRIC_SCOPES,
      loginHint: upn ?? undefined,
      prompt: 'select_account',
    });

    console.info('[fabric-token] acquireTokenPopup succeeded');
    return result.accessToken;
  } catch (err) {

    console.error('[fabric-token] acquireTokenPopup failed:', err);
    throw err;
  }
}

/** Returns a Fabric access token, using a small in-memory cache. */
export async function getFabricAccessToken(): Promise<string> {
  const now = Date.now();
  if (_cachedToken && _cachedToken.expiresOn - now > 60_000) {
    return _cachedToken.token;
  }
  const token = await _acquire(false);
  // JWT exp is in seconds since epoch, segment 1 of the dot-delimited token.
  let expiresOn = now + 30 * 60_000;
  try {
    const parts = token.split('.');
    if (parts.length >= 2) {
      // base64url -> base64 -> JSON
      const padded = parts[1]!.replace(/-/g, '+').replace(/_/g, '/');
      const json = atob(padded + '='.repeat((4 - (padded.length % 4)) % 4));
      const claims = JSON.parse(json) as { exp?: number };
      if (claims.exp) expiresOn = claims.exp * 1000;
    }
  } catch {
    /* keep default */
  }
  _cachedToken = { token, expiresOn };
  return token;
}

/** Forget any cached token (used after a 401 or on explicit sign-out). */
export function clearFabricTokenCache(): void {
  _cachedToken = null;
  workspaceTokens.clear();
}

const workspaceTokens = new Map<string, { token: string; expiresOn: number }>();

/** Workspace overrides never widen consent or borrow another cached account. */
export async function assertPreferenceOwner(owner: string): Promise<AuthMe> {
  const response = await fetch('/api/me', { credentials: 'include', cache: 'no-store' });
  if (!response.ok) throw new Error('Authenticated account configuration is unavailable.');
  const me: AuthMe = await response.json();
  if (!owner || me.configOwner !== owner) {
    throw new Error('Authenticated account changed. Reload to load that account\'s preferences before continuing.');
  }
  return me;
}

export async function getWorkspaceAccessToken(audience: 'fabric' | 'sql', owner: string): Promise<string> {
  const me = await assertPreferenceOwner(owner);
  if (!me.oid || !me.aadTenantId) throw new Error('Verified Entra identity is required for a selected workspace.');
  const key = `${me.aadTenantId}:${me.oid}:${audience}`;
  const cached = workspaceTokens.get(key);
  if (cached && cached.expiresOn - Date.now() > 60_000) return cached.token;
  const pca = await _getInstance();
  const account = pca.getAllAccounts().find(item =>
    item.localAccountId.toLowerCase() === me.oid!.toLowerCase() &&
    item.tenantId.toLowerCase() === me.aadTenantId!.toLowerCase());
  const scopes = [audience === 'fabric'
    ? 'https://api.fabric.microsoft.com/.default' : 'https://database.windows.net/.default'];
  let result;
  try {
    result = account
      ? await pca.acquireTokenSilent({ scopes, account })
      : await pca.ssoSilent({ scopes, loginHint: me.upn });
  } catch (error) {
    if (!(error instanceof InteractionRequiredAuthError) && !(error instanceof BrowserAuthError)) {
      throw new Error(`Delegated ${audience} token acquisition failed. Check your existing sign-in and permissions.`);
    }
    try {
      // prompt=none cannot grant missing consent. This runs from Validate/Send.
      result = await pca.acquireTokenPopup({ scopes, loginHint: me.upn, prompt: 'none' });
    } catch {
      throw new Error(`Existing-consented ${audience} access is unavailable. Sign in with the current account; no permissions were granted.`);
    }
  }
  if (result.account?.localAccountId.toLowerCase() !== me.oid.toLowerCase() ||
      result.account?.tenantId.toLowerCase() !== me.aadTenantId.toLowerCase() ||
      !result.accessToken || !result.expiresOn) {
    throw new Error('Delegated credentials do not match the current authenticated account.');
  }
  workspaceTokens.set(key, { token: result.accessToken, expiresOn: result.expiresOn.getTime() });
  return result.accessToken;
}
