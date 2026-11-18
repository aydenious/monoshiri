import { z } from "zod";

/**
 * A recorded capability run (YouTube, web crawl, Google search). Chat cites a run as
 * ``run_<uuid>`` and the citation panel opens it through ``GET .../scrapers/runs/{run_id}``.
 * Run origin: ``ui``, ``api`` (PAT) or ``agent`` (chat tools).
 */
export const scraperRunSummary = z.object({
	id: z.string(),
	capability: z.string(),
	origin: z.string(),
	status: z.string(),
	item_count: z.number(),
	char_count: z.number(),
	duration_ms: z.number().nullable(),
	cost_micros: z.number().nullable(),
	error: z.string().nullable(),
	created_at: z.string(),
});

export const scraperRunDetail = scraperRunSummary.extend({
	thread_id: z.string().nullable(),
	input: z.record(z.string(), z.unknown()).nullable(),
	output_text: z.string().nullable(),
	// Coarse progress log captured during the run (nullable/absent for older or
	// zero-progress runs). Each entry is a ``run.progress`` event object.
	progress: z.array(z.record(z.string(), z.unknown())).nullable().optional(),
});

export type ScraperRunDetail = z.infer<typeof scraperRunDetail>;
