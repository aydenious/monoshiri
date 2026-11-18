import type { APIRequestContext } from "@playwright/test";
import { isDocumentReady, listDocuments } from "../api/documents";

/**
 * Polls until the given document title appears in the workspace with
 * status=ready. Useful when a spec wants to assert on a specific file
 * by name rather than count.
 */
export async function waitForDocumentByTitle(
	request: APIRequestContext,
	token: string,
	workspaceId: number,
	title: string,
	options: { timeoutMs?: number; intervalMs?: number } = {}
): Promise<void> {
	const timeoutMs = options.timeoutMs ?? 60_000;
	const intervalMs = options.intervalMs ?? 1_000;
	const startedAt = Date.now();

	while (Date.now() - startedAt < timeoutMs) {
		const docs = await listDocuments(request, token, workspaceId);
		const match = docs.find((d) => d.title === title && isDocumentReady(d));
		if (match) {
			return;
		}
		await new Promise((resolve) => setTimeout(resolve, intervalMs));
	}

	throw new Error(
		`waitForDocumentByTitle: timed out after ${timeoutMs}ms waiting for ` +
			`title=${JSON.stringify(title)} in space ${workspaceId}.`
	);
}
