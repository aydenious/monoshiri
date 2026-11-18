/**
 * Curated example chat prompts shown on the empty new-chat screen.
 *
 * These mirror the homepage hero's "use case" concept but with runnable chat
 * queries, grouped into a few broad categories. Bracketed slots like `[topic]`
 * are intentional: clicking a prompt prefills the composer so the user can fill
 * them in before sending.
 *
 * This is a module-scope constant so it is created once, not per render.
 */

export interface ChatExampleCategory {
	/** Stable id used as the Tabs value */
	id: string;
	/** Short, human-readable tab label */
	label: string;
	/** Runnable example queries for this category */
	prompts: string[];
}

export const CHAT_EXAMPLE_CATEGORIES: ChatExampleCategory[] = [
	{
		id: "research",
		label: "Research the Web",
		prompts: [
			"Research [topic] across the live web and give me a cited brief",
			"Map who ranks for [keyword], crawl each result, and compare their claims in one table",
			"Which queries about [topic] trigger an AI Overview, and who gets cited?",
		],
	},
	{
		id: "listen",
		label: "Video & Comments",
		prompts: [
			"Analyze the comments on [channel]'s last 10 videos and cluster the complaints",
			"Find recent YouTube videos about [topic] and summarize what they say",
		],
	},
	{
		id: "automate",
		label: "Automate",
		prompts: [
			"Watch [url] daily and alert me on any change",
			"Search the web daily for mentions of [brand] and send me a digest",
			"Send me a weekly report on [keyword] rankings and AI Overview citations",
			"Every Monday, crawl [site]'s changelog and send me a brief",
		],
	},
];
