import { z } from "zod";

/**
 * Connector types as stored on legacy rows. The fork no longer lets users create
 * connectors, but notifications and documents that came from removed connector
 * types still exist, and their type strings must keep parsing.
 */
export const searchSourceConnectorTypeEnum = z.enum([
	"SERPER_API",
	"TAVILY_API",
	"SEARXNG_API",
	"LINKUP_API",
	"BAIDU_SEARCH_API",
	"SLACK_CONNECTOR",
	"TEAMS_CONNECTOR",
	"ONEDRIVE_CONNECTOR",
	"NOTION_CONNECTOR",
	"GITHUB_CONNECTOR",
	"LINEAR_CONNECTOR",
	"DISCORD_CONNECTOR",
	"JIRA_CONNECTOR",
	"CONFLUENCE_CONNECTOR",
	"CLICKUP_CONNECTOR",
	"GOOGLE_CALENDAR_CONNECTOR",
	"GOOGLE_GMAIL_CONNECTOR",
	"GOOGLE_DRIVE_CONNECTOR",
	"AIRTABLE_CONNECTOR",
	"LUMA_CONNECTOR",
	"ELASTICSEARCH_CONNECTOR",
	"WEBCRAWLER_CONNECTOR",
	"YOUTUBE_CONNECTOR",
	"BOOKSTACK_CONNECTOR",
	"CIRCLEBACK_CONNECTOR",
	"DROPBOX_CONNECTOR",
	"MCP_CONNECTOR",
	"OBSIDIAN_CONNECTOR",
	"COMPOSIO_GOOGLE_DRIVE_CONNECTOR",
	"COMPOSIO_GMAIL_CONNECTOR",
	"COMPOSIO_GOOGLE_CALENDAR_CONNECTOR",
]);

export type SearchSourceConnectorType = z.infer<typeof searchSourceConnectorTypeEnum>;
