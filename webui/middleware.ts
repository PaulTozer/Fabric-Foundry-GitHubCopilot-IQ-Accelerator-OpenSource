import { NextResponse, type NextRequest } from 'next/server';

export function middleware(req: NextRequest) {
  // Production headers are trusted ONLY behind the configured Entra Easy Auth
  // ingress. Never expose the underlying application port to the internet.
  const local = process.env.NODE_ENV !== 'production' && process.env.IQ_LOCAL_AUTH_BYPASS === '1';
  if (!local && !req.headers.get('x-ms-client-principal-id')) {
    return NextResponse.json({ error: 'Authentication required.' }, { status: 401 });
  }
  if (!['GET', 'HEAD', 'OPTIONS'].includes(req.method)) {
    const origin = req.headers.get('origin');
    const expected = process.env.IQ_PUBLIC_ORIGIN || `${local ? req.nextUrl.protocol : 'https:'}//${req.headers.get('host')}`;
    let valid = req.headers.get('sec-fetch-site') !== 'cross-site';
    try {
      if (origin) valid = valid && new URL(origin).origin === expected && new URL(origin).pathname === '/';
    } catch { valid = false; }
    if (!valid) {
      return NextResponse.json({ error: 'Cross-origin request refused.' }, { status: 403 });
    }
  }
  return NextResponse.next();
}
export const config = { matcher: ['/api/:path*'] };
