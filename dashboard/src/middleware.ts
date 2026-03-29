import { NextRequest, NextResponse } from "next/server";
import { jwtVerify } from "jose";

// Pages that do not require authentication
const PUBLIC_PATHS = new Set(["/login"]);

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Allow public paths and Next.js internals through
  if (
    PUBLIC_PATHS.has(pathname) ||
    pathname.startsWith("/_next") ||
    pathname.startsWith("/api/") ||
    pathname.startsWith("/engine-api/") ||
    pathname.startsWith("/favicon")
  ) {
    return NextResponse.next();
  }

  // Check for auth token in cookies (set after login) or Authorization header
  const token =
    request.cookies.get("leviathan_token")?.value ??
    request.headers.get("Authorization")?.replace("Bearer ", "");

  if (!token) {
    const loginUrl = request.nextUrl.clone();
    loginUrl.pathname = "/login";
    return NextResponse.redirect(loginUrl);
  }

  // Verify JWT signature — fail closed when JWT_SECRET is unset
  const jwtSecret = process.env.JWT_SECRET;
  if (!jwtSecret) {
    console.error("FATAL: JWT_SECRET not configured — rejecting all authenticated requests");
    const loginUrl = request.nextUrl.clone();
    loginUrl.pathname = "/login";
    return NextResponse.redirect(loginUrl);
  }
  try {
    await jwtVerify(token, new TextEncoder().encode(jwtSecret));
  } catch {
    const loginUrl = request.nextUrl.clone();
    loginUrl.pathname = "/login";
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  // Run middleware on all routes except static files
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
