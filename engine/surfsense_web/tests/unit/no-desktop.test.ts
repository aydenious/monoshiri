import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

// The Electron desktop app is not shipped; its bridge and modules were removed.
const WEB_ROOT = process.cwd();
const DELETED_MODULES = [
	'@/hooks/use-platform"',
	"@/contexts/platform-context",
	"@/components/platform-gate",
	"@/components/desktop/",
	"@/hooks/use-folder-sync",
	"@/lib/folder-sync-upload",
	"@/components/sources/FolderWatchDialog",
	"@/components/layout/ui/sidebar/LocalFilesystemBrowser",
];

test("no web source references the Electron bridge or a deleted desktop module", () => {
	const files = execFileSync("git", ["ls-files", "--", "*.ts", "*.tsx"], {
		cwd: WEB_ROOT,
		encoding: "utf8",
	})
		.split("\n")
		.filter((f) => f && existsSync(join(WEB_ROOT, f)) && !/^(node_modules|\.next|tests)\//.test(f));
	assert.ok(files.length > 100);
	const offenders = files.filter((f) => {
		const src = readFileSync(join(WEB_ROOT, f), "utf8");
		return src.includes("electronAPI") || DELETED_MODULES.some((m) => src.includes(m));
	});
	assert.deepEqual(offenders, []);
});
