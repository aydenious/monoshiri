"use client";
import { useQuery } from "@tanstack/react-query";
import type { ScraperRunDetail } from "@/contracts/types/scraper.types";
import { scrapersApiService } from "@/lib/apis/scrapers-api.service";
import { cacheKeys } from "@/lib/query-client/cache-keys";

/** Full run record (input + stored output) for a cited run. */
export function useScraperRun(
	workspaceId: number | string,
	runId: string | undefined,
	options: { enabled?: boolean } = {}
) {
	const { enabled = true } = options;
	return useQuery<ScraperRunDetail, Error>({
		queryKey: cacheKeys.scrapers.run(workspaceId, runId ?? ""),
		queryFn: () => scrapersApiService.getRun(workspaceId, runId as string),
		enabled: enabled && !!runId,
		staleTime: 30_000,
	});
}
