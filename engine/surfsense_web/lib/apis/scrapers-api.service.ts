import { scraperRunDetail } from "@/contracts/types/scraper.types";
import { baseApiService } from "./base-api.service";

const base = (workspaceId: number | string) => `/api/v1/workspaces/${workspaceId}/scrapers`;

class ScrapersApiService {
	/** One recorded capability run (YouTube / web crawl / search), opened from a chat citation. */
	getRun = async (workspaceId: number | string, runId: string) => {
		return baseApiService.get(`${base(workspaceId)}/runs/${runId}`, scraperRunDetail);
	};
}

export const scrapersApiService = new ScrapersApiService();
