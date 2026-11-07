import type { NextRequest } from "next/server";
import { clientIpHeaders, loginAndSetCookie } from "@/lib/session";

// POST /api/auth/login  { email, password, remember }
export async function POST(request: NextRequest) {
  const { email, password, remember } = await request.json().catch(() => ({}));
  return loginAndSetCookie(String(email ?? ""), String(password ?? ""), Boolean(remember), clientIpHeaders(request));
}
