/** Serialise validated query params; arrays become comma-joined values. Empty when absent. */
export function toQueryString(params?: object | null): string {
	if (!params) return "";
	return new URLSearchParams(
		Object.entries(params as Record<string, unknown>).map(([k, v]) => [
			k,
			Array.isArray(v) ? v.join(",") : String(v),
		])
	).toString();
}
