import { handleUnauthorized, refreshSession } from "@/lib/auth-utils";

type AuthenticatedFetchOptions = RequestInit & {
	skipAuthRedirect?: boolean;
	skipRefresh?: boolean;
};

export function getAuthHeaders(additionalHeaders?: Record<string, string>): Record<string, string> {
	return { ...additionalHeaders };
}

function fetchWithAuth(url: string, options: RequestInit): Promise<Response> {
	return fetch(url, {
		...options,
		headers: new Headers(options.headers),
		credentials: options.credentials ?? "include",
	});
}

export async function authenticatedFetch(
	url: string,
	options: AuthenticatedFetchOptions = {}
): Promise<Response> {
	const { skipAuthRedirect = false, skipRefresh = false, ...fetchOptions } = options;

	const response = await fetchWithAuth(url, fetchOptions);

	if (response.status !== 401) {
		return response;
	}

	let unauthorizedResponse = response;
	if (!skipRefresh) {
		const refreshed = await refreshSession();
		if (refreshed) {
			const retryResponse = await fetchWithAuth(url, fetchOptions);
			if (retryResponse.status !== 401) {
				return retryResponse;
			}
			unauthorizedResponse = retryResponse;
		}
	}

	if (!skipAuthRedirect) {
		handleUnauthorized();
		throw new Error("Unauthorized: Redirecting to login page");
	}

	return unauthorizedResponse;
}
