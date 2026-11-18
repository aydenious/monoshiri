import { type NextRequest, NextResponse } from "next/server";
import { BUILD_TIME_AUTH_TYPE } from "@/lib/env-config";
import { redirectTarget } from "@/lib/monoshiri-redirects";
import { RUNTIME_AUTH_TYPE_COOKIE_NAME, resolveRuntimeAuthUiMode } from "@/lib/runtime-auth-config";

// The backend's session cookies (see app/auth/session_cookies.py). The access
// cookie is short-lived, so the refresh cookie also counts as "has a session":
// the dashboard refreshes it on load.
function hasSessionCookie(request: NextRequest): boolean {
	const sessionName = process.env.SESSION_COOKIE_NAME || "surfsense_session";
	const refreshName = process.env.REFRESH_COOKIE_NAME || "surfsense_refresh";
	return Boolean(
		request.cookies.get(sessionName)?.value || request.cookies.get(refreshName)?.value
	);
}

export function proxy(request: NextRequest) {
	const target = redirectTarget(request.nextUrl.pathname, hasSessionCookie(request));
	if (target) {
		const destination = request.nextUrl.clone();
		destination.pathname = target;
		destination.search = "";
		return NextResponse.redirect(destination);
	}

	const response = NextResponse.next();
	const authType = resolveRuntimeAuthUiMode(process.env.AUTH_TYPE, BUILD_TIME_AUTH_TYPE);

	response.cookies.set(RUNTIME_AUTH_TYPE_COOKIE_NAME, authType, {
		path: "/",
		maxAge: 60 * 60 * 24 * 365,
		sameSite: "lax",
		secure: request.nextUrl.protocol === "https:",
	});

	return response;
}

export const config = {
	matcher: ["/((?!api|auth|_next/static|_next/image|favicon.ico|.*\\..*).*)"],
};
