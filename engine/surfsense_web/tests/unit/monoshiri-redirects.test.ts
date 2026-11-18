import assert from "node:assert/strict";
import test from "node:test";

// Run with: pnpm exec tsx --test tests/unit/monoshiri-redirects.test.ts

import { redirectTarget } from "../../lib/monoshiri-redirects";

const MARKETING_OR_UNKNOWN = [
	"/",
	"/pricing",
	"/blog",
	"/blog/x",
	"/downloads",
	"/free",
	"/sunset",
	"/changelog",
	"/license",
	"/privacy",
	"/terms",
	"/docs",
	"/docs/anything",
	"/connectors/reddit",
	"/some/unknown/path",
	"/public/x",
	"/public/abc123/chat",
	"/invite/abc", // workspace invite page removed (single-user)
];

for (const path of MARKETING_OR_UNKNOWN) {
	test(`${path} redirects to /login without a session`, () => {
		assert.equal(redirectTarget(path, false), "/login");
	});
	test(`${path} redirects to /dashboard with a session`, () => {
		assert.equal(redirectTarget(path, true), "/dashboard");
	});
}

const REMOVED_DASHBOARD_PATHS = [
	"/dashboard/buy-tokens",
	"/dashboard/7/buy-more",
	"/dashboard/7/buy-pages",
	"/dashboard/7/earn-credits",
	"/dashboard/7/more-pages",
	"/dashboard/7/purchase-success",
	"/dashboard/7/purchase-cancel",
	"/dashboard/7/user-settings/purchases",
	"/dashboard/7/connectors",
	"/dashboard/7/connectors/callback",
];

for (const path of REMOVED_DASHBOARD_PATHS) {
	test(`${path} redirects to /login without a session`, () => {
		assert.equal(redirectTarget(path, false), "/login");
	});
	test(`${path} redirects to /dashboard with a session`, () => {
		assert.equal(redirectTarget(path, true), "/dashboard");
	});
}

const APP_PATHS = [
	"/login",
	"/register",
	"/auth/callback",
	"/auth/jwt/login",
	"/dashboard",
	"/dashboard/7",
	"/api/zero/query",
	"/zero/query",
	"/_next/static/chunk.js",
	"/robots.txt",
	"/favicon.ico",
	"/icon.svg",
	"/images/logo.png",
	"/verify-token",
];

for (const path of APP_PATHS) {
	for (const session of [true, false]) {
		test(`${path} is left alone (session=${session})`, () => {
			assert.equal(redirectTarget(path, session), null);
		});
	}
}

test("prefix lookalikes are not treated as app paths", () => {
	assert.equal(redirectTarget("/loginx", false), "/login");
	assert.equal(redirectTarget("/dashboardx", true), "/dashboard");
	assert.equal(redirectTarget("/authors", false), "/login");
});

test("the removed desktop routes are redirected like any other unknown path", () => {
	assert.equal(redirectTarget("/desktop/login", false), "/login");
	assert.equal(redirectTarget("/desktop/login", true), "/dashboard");
});
