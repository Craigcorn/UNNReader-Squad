// Run every `*.test.mts` under src/, whether or not anyone remembered to
// register it.
//
// CI ran exactly one of the eight suites, because the workflow named a single
// npm script by hand and nobody updated it as suites were added. A contributor
// could break the replay decoder and watch CI stay green. Finding the files
// rather than listing them removes the whole class of mistake: a new test is
// picked up by existing.
//
// Bundling goes through esbuild's JS API rather than its executable — a `.cmd`
// shim cannot be spawned directly on Windows (EINVAL), and a contributor whose
// tests will not run is a contributor who stops writing them. Each suite then
// runs in its own node process, because they signal failure with process.exit.
import { readdirSync, statSync, mkdirSync } from "node:fs";
import { join, relative } from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const root = fileURLToPath(new URL("..", import.meta.url));
const src = join(root, "src");
const outDir = join(root, "node_modules", ".cache", "tests");

function find(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...find(p));
    else if (name.endsWith(".test.mts")) out.push(p);
  }
  return out.sort();
}

const files = find(src);
if (!files.length) {
  console.error("no *.test.mts found under src/ — that is almost certainly wrong");
  process.exit(1);
}
mkdirSync(outDir, { recursive: true });

let failed = 0;
for (const file of files) {
  const name = relative(src, file).replace(/[\\/]/g, "_").replace(/\.mts$/, ".mjs");
  const out = join(outDir, name);
  try {
    await build({
      entryPoints: [file], bundle: true, platform: "node", format: "esm",
      outfile: out, logLevel: "warning", loader: { ".json": "json" },
    });
  } catch {
    failed++;
    console.error(`BUILD FAILED: ${relative(root, file)}`);
    continue;
  }
  try {
    execFileSync(process.execPath, [out], { stdio: "inherit" });
  } catch {
    failed++;
    console.error(`FAILED: ${relative(root, file)}`);
  }
}

console.log(`\n${files.length - failed}/${files.length} suites passed`);
process.exit(failed ? 1 : 0);
