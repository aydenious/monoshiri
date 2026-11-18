import { toast } from "sonner";
import { AbortedError, AppError, AuthenticationError } from "./error";

/**
 * Show a standardized error toast.
 *
 * Suppressed for:
 * - AbortedError (user-initiated cancellation)
 * - AuthenticationError (handled by redirect)
 */
export function showErrorToast(error: unknown, fallbackMessage?: string) {
	if (error instanceof AbortedError || error instanceof AuthenticationError) {
		return;
	}

	const message =
		error instanceof AppError
			? error.message
			: error instanceof Error
				? error.message
				: (fallbackMessage ?? "An unexpected error occurred.");

	const code = error instanceof AppError ? error.code : undefined;
	const requestId = error instanceof AppError ? error.requestId : undefined;

	const descParts: string[] = [];
	if (code) descParts.push(`Error: ${code}`);
	if (requestId) descParts.push(`ID: ${requestId}`);

	toast.error(message, {
		// A dropped connection fails every in-flight query at once, and each one
		// lands here separately. A shared id makes sonner replace rather than
		// stack, so one blip reads as one problem instead of ten.
		id: code === "NETWORK_ERROR" ? "network-error" : undefined,
		description: descParts.length > 0 ? descParts.join(" | ") : undefined,
		duration: 8000,
	});
}
