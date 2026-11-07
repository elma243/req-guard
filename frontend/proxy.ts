// Runs before every page request (Next.js 16 "proxy", formerly "middleware").
// Optimistic check only: is there a token cookie? The backend still checks the token itself.

import { NextResponse, type NextRequest } from "next/server";
import { TOKEN_COOKIE } from "@/lib/session";

// For people who are not logged in; anyone who is goes on to the app
const GUEST_PAGES = ["/login", "/register", "/verify-email", "/forgot-password"];
// A reset link must work whatever the browser holds, such as a cookie from a session that has ended
const OPEN_PAGES = ["/reset-password"];

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const loggedIn = request.cookies.has(TOKEN_COOKIE);
  const isGuestPage = GUEST_PAGES.includes(pathname);
  if (OPEN_PAGES.includes(pathname)) return NextResponse.next();

  if (!loggedIn && !isGuestPage) {
    const url = new URL("/login", request.url);
    if (pathname !== "/") url.searchParams.set("next", pathname + search);
    return NextResponse.redirect(url);
  }
  if (loggedIn && isGuestPage) {
    return NextResponse.redirect(new URL("/projects", request.url));
  }
  return NextResponse.next();
}

export const config = {
  // Skip API routes, Next.js internals and files with an extension (favicon, images, ...)
  matcher: ["/((?!api|_next/static|_next/image|.*\\..*).*)"],
};
