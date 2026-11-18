import assert from "node:assert/strict";
import test from "node:test";

// Run with: pnpm test:unit

import type { NextRequest } from "next/server";
import { createBackendProxy } from "../../lib/server/backend-proxy";

const SURFSENSE_URL = "SURFSENSE_BACKEND_INTERNAL_URL";
const FASTAPI_URL = "FASTAPI_BACKEND_INTERNAL_URL";

type FetchCall = { url: string; init: RequestInit & { duplex?: string } };

type StubReply = {
	status?: number;
	statusText?: string;
	headers?: Record<string, string>;
	body?: ReadableStream<Uint8Array> | null;
};

/** Replaces global fetch, returning the recorded calls and a restore function. */
function stubFetch(reply: StubReply = {}) {
	const calls: FetchCall[] = [];
	const original = globalThis.fetch;
	globalThis.fetch = (async (input: URL | RequestInfo, init?: RequestInit) => {
		calls.push({ url: String(input), init: init ?? {} });
		return {
			status: reply.status ?? 200,
			statusText: reply.statusText ?? "OK",
			headers: new Headers(reply.headers),
			body: reply.body ?? null,
		} as unknown as Response;
	}) as typeof fetch;
	return {
		calls,
		restore: () => {
			globalThis.fetch = original;
		},
	};
}

/** The handler only reads method, headers, body and nextUrl.search. */
function fakeRequest({
	method = "GET",
	headers = {},
	search = "",
	body = null,
}: {
	method?: string;
	headers?: Record<string, string>;
	search?: string;
	body?: ReadableStream<Uint8Array> | null;
} = {}) {
	return {
		method,
		headers: new Headers(headers),
		body,
		nextUrl: { search },
	} as unknown as NextRequest;
}

async function withEnv<T>(
	env: Record<string, string | undefined>,
	run: () => Promise<T>
): Promise<T> {
	const previous: Record<string, string | undefined> = {
		[SURFSENSE_URL]: process.env[SURFSENSE_URL],
		[FASTAPI_URL]: process.env[FASTAPI_URL],
	};
	for (const [key, value] of Object.entries(env)) {
		if (value === undefined) {
			delete process.env[key];
		} else {
			process.env[key] = value;
		}
	}
	try {
		return await run();
	} finally {
		for (const [key, value] of Object.entries(previous)) {
			if (value === undefined) {
				delete process.env[key];
			} else {
				process.env[key] = value;
			}
		}
	}
}

const PREFIX_CASES = [
	{ prefix: "/auth", path: ["users", "me"], expected: "http://backend:8000/auth/users/me" },
	{
		prefix: "/api/v1",
		path: ["documents", "7"],
		expected: "http://backend:8000/api/v1/documents/7",
	},
];

for (const { prefix, path, expected } of PREFIX_CASES) {
	test(`${prefix} joins the path onto the upstream base and keeps the query string`, async () => {
		const { calls, restore } = stubFetch();
		try {
			await withEnv({ [SURFSENSE_URL]: "http://backend:8000" }, async () => {
				const proxy = createBackendProxy(prefix);
				await proxy(fakeRequest({ search: "?page=2&search=a%20b" }), {
					params: Promise.resolve({ path }),
				});
			});
		} finally {
			restore();
		}
		assert.equal(calls.length, 1);
		assert.equal(calls[0].url, `${expected}?page=2&search=a%20b`);
	});

	test(`${prefix} trims a trailing slash from the upstream base`, async () => {
		const { calls, restore } = stubFetch();
		try {
			await withEnv({ [SURFSENSE_URL]: "http://backend:8000/" }, async () => {
				const proxy = createBackendProxy(prefix);
				await proxy(fakeRequest(), { params: Promise.resolve({ path }) });
			});
		} finally {
			restore();
		}
		assert.equal(calls[0].url, expected);
	});
}

test("falls back to FASTAPI_BACKEND_INTERNAL_URL, then to http://backend:8000", async () => {
	const proxy = createBackendProxy("/auth");

	const legacy = stubFetch();
	try {
		await withEnv({ [SURFSENSE_URL]: undefined, [FASTAPI_URL]: "http://legacy:9000" }, async () => {
			await proxy(fakeRequest(), { params: Promise.resolve({ path: ["token"] }) });
		});
	} finally {
		legacy.restore();
	}
	assert.equal(legacy.calls[0].url, "http://legacy:9000/auth/token");

	const fallback = stubFetch();
	try {
		await withEnv({ [SURFSENSE_URL]: undefined, [FASTAPI_URL]: undefined }, async () => {
			await proxy(fakeRequest(), { params: Promise.resolve({ path: ["token"] }) });
		});
	} finally {
		fallback.restore();
	}
	assert.equal(fallback.calls[0].url, "http://backend:8000/auth/token");
});

test("an empty path keeps the prefix's trailing slash", async () => {
	const { calls, restore } = stubFetch();
	try {
		await withEnv({ [SURFSENSE_URL]: "http://backend:8000" }, async () => {
			const proxy = createBackendProxy("/auth");
			await proxy(fakeRequest(), { params: Promise.resolve({}) });
		});
	} finally {
		restore();
	}
	assert.equal(calls[0].url, "http://backend:8000/auth/");
});

test("drops host and content-length upstream and keeps the rest", async () => {
	const { calls, restore } = stubFetch();
	try {
		await withEnv({ [SURFSENSE_URL]: "http://backend:8000" }, async () => {
			const proxy = createBackendProxy("/auth");
			await proxy(
				fakeRequest({
					method: "POST",
					headers: {
						host: "localhost:3000",
						"content-length": "5",
						authorization: "Bearer token",
						"content-type": "application/json",
					},
					body: new Blob(["hello"]).stream(),
				}),
				{ params: Promise.resolve({ path: ["login"] }) }
			);
		});
	} finally {
		restore();
	}

	const headers = calls[0].init.headers as Headers;
	assert.equal(headers.get("host"), null);
	assert.equal(headers.get("content-length"), null);
	assert.equal(headers.get("authorization"), "Bearer token");
	assert.equal(headers.get("content-type"), "application/json");
});

test("drops hop-by-hop headers downstream and passes the status through", async () => {
	const { restore } = stubFetch({
		status: 418,
		statusText: "I'm a teapot",
		headers: {
			connection: "keep-alive",
			"keep-alive": "timeout=5",
			"proxy-authenticate": "Basic",
			"proxy-authorization": "Basic",
			te: "trailers",
			trailer: "x-checksum",
			"transfer-encoding": "chunked",
			upgrade: "h2c",
			"x-request-id": "abc123",
			"content-type": "application/json",
		},
	});

	let response: Response;
	try {
		response = await withEnv({ [SURFSENSE_URL]: "http://backend:8000" }, async () => {
			const proxy = createBackendProxy("/auth");
			return proxy(fakeRequest(), { params: Promise.resolve({ path: ["boom"] }) });
		});
	} finally {
		restore();
	}

	assert.equal(response.status, 418);
	assert.equal(response.statusText, "I'm a teapot");
	for (const header of [
		"connection",
		"keep-alive",
		"proxy-authenticate",
		"proxy-authorization",
		"te",
		"trailer",
		"transfer-encoding",
		"upgrade",
	]) {
		assert.equal(response.headers.get(header), null, `${header} leaked downstream`);
	}
	assert.equal(response.headers.get("x-request-id"), "abc123");
	assert.equal(response.headers.get("content-type"), "application/json");
});

test("streams the response body back to the client", async () => {
	const { restore } = stubFetch({ body: new Blob(["upstream body"]).stream() });
	let response: Response;
	try {
		response = await withEnv({ [SURFSENSE_URL]: "http://backend:8000" }, async () => {
			const proxy = createBackendProxy("/auth");
			return proxy(fakeRequest(), { params: Promise.resolve({ path: ["x"] }) });
		});
	} finally {
		restore();
	}
	assert.equal(await response.text(), "upstream body");
});

for (const method of ["GET", "HEAD"]) {
	test(`${method} sends no body and follows no redirect`, async () => {
		const { calls, restore } = stubFetch();
		try {
			await withEnv({ [SURFSENSE_URL]: "http://backend:8000" }, async () => {
				const proxy = createBackendProxy("/auth");
				await proxy(fakeRequest({ method }), { params: Promise.resolve({ path: ["health"] }) });
			});
		} finally {
			restore();
		}
		assert.equal(calls[0].init.method, method);
		assert.equal(calls[0].init.body, undefined);
		assert.equal(calls[0].init.duplex, undefined);
		assert.equal(calls[0].init.redirect, "manual");
	});
}

for (const method of ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"]) {
	test(`${method} streams the request body with duplex: half`, async () => {
		const body = new Blob(["payload"]).stream();
		const { calls, restore } = stubFetch();
		try {
			await withEnv({ [SURFSENSE_URL]: "http://backend:8000" }, async () => {
				const proxy = createBackendProxy("/api/v1");
				await proxy(fakeRequest({ method, body }), { params: Promise.resolve({ path: ["docs"] }) });
			});
		} finally {
			restore();
		}
		assert.equal(calls[0].init.method, method);
		assert.equal(calls[0].init.body, body);
		assert.equal(calls[0].init.duplex, "half");
		assert.equal(calls[0].init.redirect, "manual");
	});
}
