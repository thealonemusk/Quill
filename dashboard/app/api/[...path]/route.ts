import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

const API_ORIGIN = (process.env.API_ORIGIN || 'https://quill-ieny.onrender.com').replace(/\/$/, '');
const ALLOWED_ORIGINS = new Set([
  'https://quill-nikhil.vercel.app',
  'http://localhost:3000',
  'http://127.0.0.1:3000',
]);
const MAX_BYTES = 6 * 1024 * 1024;

function forbidden() {
  return NextResponse.json({ detail: 'Forbidden' }, { status: 403, headers: { 'Cache-Control': 'no-store' } });
}

function sameSite(req: NextRequest): boolean {
  const site = req.headers.get('sec-fetch-site');
  if (site && site !== 'same-origin' && site !== 'none') return false;
  const origin = req.headers.get('origin');
  if (origin && !ALLOWED_ORIGINS.has(origin)) return false;
  if (req.method !== 'GET' && req.method !== 'HEAD' && req.method !== 'OPTIONS') {
    if (!origin || !ALLOWED_ORIGINS.has(origin)) return false;
  }
  return true;
}

function safePath(parts: string[]): string | null {
  if (!parts.length) return null;
  for (const part of parts) {
    if (!part || part === '.' || part === '..' || part.includes('\\') || part.includes('/')) return null;
  }
  return parts.map((part) => encodeURIComponent(part)).join('/');
}

async function proxy(req: NextRequest, parts: string[]) {
  if (!sameSite(req)) return forbidden();
  const path = safePath(parts);
  if (!path) return forbidden();
  const token = (process.env.API_TOKEN || '').trim();
  if (!token) {
    return NextResponse.json(
      { detail: 'API token is not configured' },
      { status: 401, headers: { 'Cache-Control': 'no-store' } },
    );
  }

  const headers = new Headers();
  const contentType = req.headers.get('content-type');
  if (contentType) headers.set('content-type', contentType);
  headers.set('authorization', `Bearer ${token}`);

  let body: ArrayBuffer | undefined;
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    body = await req.arrayBuffer();
    if (body.byteLength > MAX_BYTES) {
      return NextResponse.json(
        { detail: 'Payload too large' },
        { status: 413, headers: { 'Cache-Control': 'no-store' } },
      );
    }
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${API_ORIGIN}/api/${path}${req.nextUrl.search}`, {
      method: req.method,
      headers,
      body: body && body.byteLength ? body : undefined,
      redirect: 'manual',
      cache: 'no-store',
    });
  } catch {
    return NextResponse.json(
      { detail: 'Backend unavailable' },
      { status: 502, headers: { 'Cache-Control': 'no-store' } },
    );
  }

  const out = new Headers();
  const type = upstream.headers.get('content-type');
  if (type) out.set('content-type', type);
  out.set('cache-control', 'no-store');
  out.set('x-content-type-options', 'nosniff');
  return new NextResponse(upstream.body, { status: upstream.status, headers: out });
}

type Ctx = { params: { path: string[] } };

export function GET(req: NextRequest, ctx: Ctx) {
  return proxy(req, ctx.params.path);
}

export function POST(req: NextRequest, ctx: Ctx) {
  return proxy(req, ctx.params.path);
}

export function PUT(req: NextRequest, ctx: Ctx) {
  return proxy(req, ctx.params.path);
}

export function PATCH(req: NextRequest, ctx: Ctx) {
  return proxy(req, ctx.params.path);
}

export function DELETE(req: NextRequest, ctx: Ctx) {
  return proxy(req, ctx.params.path);
}

export function OPTIONS(req: NextRequest) {
  if (!sameSite(req)) return forbidden();
  return new NextResponse(null, { status: 204, headers: { 'Cache-Control': 'no-store' } });
}
