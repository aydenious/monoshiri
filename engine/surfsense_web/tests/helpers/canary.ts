import { randomUUID } from "node:crypto";

/**
 * Canary tokens & deterministic test data.
 *
 * Specs assert these strings appear in the resulting Document rows (and chat
 * answers) to prove the upload -> ETL -> indexing -> retrieval pipeline ran
 * end-to-end.
 */
export const CANARY_TOKENS = {
	manualUploadMdCanary: "E2E-MANUAL-UPLOAD-MD-CANARY-7f3a",
	manualUploadPdfCanary: "E2E-MANUAL-UPLOAD-PDF-CANARY-9d2b",
} as const;

/** Generate a unique-per-run workspace name. Keeps parallel tests isolated. */
export function uniqueWorkspaceName(prefix = "e2e"): string {
	return `${prefix}-${randomUUID().slice(0, 8)}`;
}
