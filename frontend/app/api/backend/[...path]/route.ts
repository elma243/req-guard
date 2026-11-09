// Forwards /api/backend/<path> to FastAPI <path>, adding the bearer token from the httpOnly cookie.
// The browser only ever talks to this same-origin route, so the token never reaches client JavaScript.

import { NextResponse, type NextRequest } from "next/server";
import { BACKEND_URL, TOKEN_COOKIE, backendTimeout, backendUnreachable, clientIpHeaders } from "@/lib/session";

// Longest wait for FastAPI. Must stay longer than the backend's longest request: requirement extraction
// ends after 240 s at the latest, LLM retries included (RUN_TIME_BUDGET_SECONDS in
// backend/app/nlp/extraction.py). Must stay below Node's own 300 s limit for response headers.
const BACKEND_TIMEOUT_MS = 285_000;

// Lets hosting platforms (Vercel) run this route as long as the timeout above
export const maxDuration = 300;

type Context = { params: Promise<{ path: string[] }> };

async function forward(request: NextRequest, { params }: Context) {
  const { path } = await params;
  const url = `${BACKEND_URL}/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;

  // Built from scratch, so X-Client-IP or X-Proxy-Secret sent by a browser never reach FastAPI
  const headers = new Headers(clientIpHeaders(request));
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  // Audio players ask for byte ranges (to find the length and to seek)
  const range = request.headers.get("range");
  if (range) headers.set("range", range);
  const token = request.cookies.get(TOKEN_COOKIE)?.value;
  if (token) headers.set("authorization", `Bearer ${token}`);

  const hasBody = !["GET", "HEAD"].includes(request.method);

  let res: Response;
  let body: ArrayBuffer | null;
  try {
    res = await fetch(url, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      cache: "no-store",
      signal: AbortSignal.timeout(BACKEND_TIMEOUT_MS),
    });
    body = res.status === 204 ? null : await res.arrayBuffer();
  } catch (err) {
    if (err instanceof DOMException && err.name === "TimeoutError") return backendTimeout(BACKEND_TIMEOUT_MS / 1000);
    return backendUnreachable();
  }

  const response = new NextResponse(body, { status: res.status });
  // Headers a media player needs: type, length and byte ranges (206 Partial Content keeps its status above)
  for (const name of [
    "content-type",
    "content-range",
    "accept-ranges",
    "content-disposition",
    "cache-control",
  ]) {
    const value = res.headers.get(name);
    if (value) response.headers.set(name, value);
  }
  // The length of what is sent here (fetch may have decoded a compressed body)
  if (body) response.headers.set("content-length", String(body.byteLength));
  const retryAfter = res.headers.get("retry-after");
  if (retryAfter) response.headers.set("retry-after", retryAfter);

  // Token expired or invalid: drop the cookie so proxy.ts sends the user to /login
  if (res.status === 401) response.cookies.delete(TOKEN_COOKIE);
  return response;
}

export { forward as GET, forward as POST, forward as PUT, forward as PATCH, forward as DELETE };
