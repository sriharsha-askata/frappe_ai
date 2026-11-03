import { mkdtempSync, readdirSync, rmSync } from "node:fs";
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

const testsDir = path.join(rootDir, "tests");
const testFiles = readdirSync(testsDir).filter((name) => name.endsWith(".test.ts")).sort();
const esbuild = loadFromFallback("esbuild");
const outputDir = mkdtempSync(path.join(tmpdir(), "frappe-ai-tests-"));
let failed = false;
try {
	for (const name of testFiles) {
		const output = path.join(outputDir, name.replace(/\.ts$/, ".cjs"));
		await esbuild.build({
			entryPoints: [path.join(testsDir, name)],
			outfile: output,
			bundle: true,
			platform: "node",
			format: "cjs",
			target: "node18",
			external: ["node:*"],
		});
		const result = spawnSync(process.execPath, [output], { stdio: "inherit" });
		if (result.error) throw result.error;
		if (result.status !== 0) failed = true;
	}
	process.exitCode = failed ? 1 : 0;
} finally {
	rmSync(outputDir, { recursive: true, force: true });
}
