"use client";

import { Button } from "@/components/ui/button";

export default function ErrorPage({
	error,
	reset,
}: {
	error: globalThis.Error & { digest?: string; code?: string; requestId?: string };
	reset: () => void;
}) {
	return (
		<div className="flex min-h-[50vh] flex-col items-center justify-center gap-4 text-center px-4">
			<h2 className="text-2xl font-semibold">Something went wrong</h2>
			<p className="text-muted-foreground max-w-md">
				An unexpected error occurred. Please try again.
			</p>

			{(error.digest || error.code || error.requestId) && (
				<div className="rounded-md border bg-muted/50 px-4 py-2 text-xs text-muted-foreground font-mono max-w-md">
					{error.code && <span>Code: {error.code}</span>}
					{error.requestId && <span className="ml-3">ID: {error.requestId}</span>}
					{error.digest && <span className="ml-3">Digest: {error.digest}</span>}
				</div>
			)}

			<div className="flex gap-2">
				<Button type="button" onClick={reset}>
					Try again
				</Button>
			</div>
		</div>
	);
}
