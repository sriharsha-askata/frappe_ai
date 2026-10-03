import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { spawnSync } from "node:child_process";

const require = createRequire(import.meta.url);
const rootDir = path.resolve(path.dirname(new URL(import.meta.url).pathname));
const factoryNodeModules = path.resolve(rootDir, "../../factory_automation_api/fact-frontend/node_modules");

function loadFromFallback(moduleName) {
	try {
		return require(moduleName);
	} catch {
		return createRequire(path.join(factoryNodeModules, "package.json"))(moduleName);
	}
}

const outputDir = mkdtempSync(path.join(tmpdir(), "frappe-ai-events-"));
try {
	const output = path.join(outputDir, "runEvents.test.cjs");
	await loadFromFallback("esbuild").build({
		entryPoints: [path.join(rootDir, "tests/runEvents.test.ts")],
		outfile: output,
		bundle: true,
		platform: "node",
		format: "cjs",
		target: "node18",
		external: ["node:*"],
	});
	const result = spawnSync(process.execPath, [output], { stdio: "inherit" });
	if (result.error) throw result.error;
	process.exitCode = result.status ?? 1;
} finally {
	rmSync(outputDir, { recursive: true, force: true });
}
