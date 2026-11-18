/**
 * Central export of all Playwright fixtures used across the E2E suite.
 *
 * Specs import `test` and `expect` from here instead of `@playwright/test`
 * directly so that adding a new fixture is a one-line change for every spec
 * that needs it.
 *
 * Inheritance chain:
 *   base (@playwright/test)
 *     └─ workspaceFixtures   — apiToken, workspace
 *         └─ manualUploadWithChatTest — chatThread
 */
export { expect } from "@playwright/test";
export { chatThreadFixtures } from "./chat-thread.fixture";
export { workspaceFixtures } from "./workspace.fixture";

import { type ChatThreadFixtures, chatThreadFixtures } from "./chat-thread.fixture";
import { workspaceFixtures } from "./workspace.fixture";

/** Default `test` for specs that just need auth + a clean workspace. */
export const test = workspaceFixtures;
/**
 * `test` for manual upload specs that also need a chat thread.
 *
 * The user uploads files directly via the Documents-sidebar UI, so this
 * composes chat onto the bare workspace.
 */
export const manualUploadWithChatTest =
	workspaceFixtures.extend<ChatThreadFixtures>(chatThreadFixtures);
