import { mkdirSync, rmSync } from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const rootDir = path.resolve(path.dirname(new URL(import.meta.url).pathname));
const factoryNodeModules = path.resolve(rootDir, "../../factory_automation_api/fact-frontend/node_modules");
const localNodeModules = path.resolve(rootDir, "node_modules");

function loadFromFallback(moduleName) {
	try {
		return require(moduleName);
	} catch {
		const fallbackRequire = createRequire(path.join(factoryNodeModules, "package.json"));
		return fallbackRequire(moduleName);
	}
}

const esbuild = loadFromFallback("esbuild");
const outdir = path.resolve(rootDir, "../frappe_ai/public/frappe_ai_panel");
const watch = process.argv.includes("--watch");

rmSync(outdir, { recursive: true, force: true });
mkdirSync(outdir, { recursive: true });

const config = {
	entryPoints: [path.resolve(rootDir, "src/main.tsx")],
	outdir,
	entryNames: "frappe_ai_panel",
	assetNames: "assets/[name]",
	bundle: true,
	format: "iife",
	globalName: "FrappeAIPanel",
	target: ["es2019"],
	jsx: "automatic",
	legalComments: "none",
	loader: {
		".css": "css",
		".ts": "ts",
		".tsx": "tsx",
	},
	nodePaths: [localNodeModules, factoryNodeModules],
	define: {
		"process.env.NODE_ENV": JSON.stringify(watch ? "development" : "production"),
	},
	minify: false,
	sourcemap: false,
};

if (watch) {
	const ctx = await esbuild.context(config);
	await ctx.watch();
	console.log("Watching frappe_ai panel sources...");
} else {
	await esbuild.build(config);
}
