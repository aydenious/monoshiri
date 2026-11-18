/** What the browser tells the backend about the agent filesystem: always the cloud one. */
export const BROWSER_FILESYSTEM_SELECTION = {
	filesystem_mode: "cloud",
	client_platform: "web",
} as const;
