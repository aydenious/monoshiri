/**
 * Pure routing table for Monoshiri's single-user shell.
 *
 * The marketing site is gone, so any URL that is not part of the app (old
 * bookmarks, /pricing, /blog/..., /public/<token>, ...) is sent to the
 * dashboard when a session cookie is present and to the login page otherwise.
 * Returns null for routes that must be served as-is.
 */

/** Path prefixes (whole segments) that belong to the app and are never redirected. */
const APP_PREFIXES = [
	"/dashboard",
	"/login",
	"/register",
	"/auth",
	"/api",
	"/zero",
	"/_next",
	"/verify-token",
];

/**
 * Billing and connector pages that no longer exist. They live under /dashboard, so they are matched first
 * and bounced to the dashboard (`/dashboard/buy-tokens`, `/dashboard/7/buy-more`, ...).
 */
const REMOVED_DASHBOARD_SEGMENTS = new Set([
	"buy-more",
	"buy-pages",
	"buy-tokens",
	"connectors",
	"earn-credits",
	"more-pages",
	"purchase-cancel",
	"purchase-success",
	"purchases",
]);

function isRemovedDashboardPath(pathname: string): boolean {
	if (!matchesPrefix(pathname, "/dashboard")) return false;
	return pathname.split("/").some((segment) => REMOVED_DASHBOARD_SEGMENTS.has(segment));
}

function matchesPrefix(pathname: string, prefix: string): boolean {
	return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

/** Static assets (robots.txt, favicon.ico, icon.svg, /images/x.png ...). */
function looksLikeAsset(pathname: string): boolean {
	const last = pathname.slice(pathname.lastIndexOf("/") + 1);
	return last.includes(".");
}

export function redirectTarget(pathname: string, hasSession: boolean): string | null {
	if (isRemovedDashboardPath(pathname)) return hasSession ? "/dashboard" : "/login";
	if (APP_PREFIXES.some((prefix) => matchesPrefix(pathname, prefix))) return null;
	if (looksLikeAsset(pathname)) return null;
	return hasSession ? "/dashboard" : "/login";
}
