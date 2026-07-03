import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = new URL("..", import.meta.url).pathname.replace(/^\/([A-Za-z]:\/)/, "$1");

function fail(message) {
  console.error(`FAIL node-check: ${message}`);
  process.exitCode = 1;
}

function readJson(relativePath) {
  return JSON.parse(readFileSync(join(root, relativePath), "utf8"));
}

const pkg = readJson("package.json");

if (pkg.name !== "freetier-atlas") {
  fail("package.json name must be freetier-atlas");
}

if (pkg.private !== true) {
  fail("package.json must remain private until publication packaging is intentional");
}

if (pkg.license !== "AGPL-3.0-only") {
  fail("package.json license must be AGPL-3.0-only");
}

if (!pkg.scripts || pkg.scripts.test !== "node tools/node_check.mjs") {
  fail("package.json test script must run node tools/node_check.mjs");
}

const trackedFiles = execFileSync("git", ["ls-files", "--cached", "--others", "--exclude-standard"], {
  cwd: root,
  encoding: "utf8"
})
  .split(/\r?\n/)
  .filter(Boolean);

for (const relativePath of trackedFiles) {
  if (!/\.(js|mjs|json|md|ps1|py|sh|toml|ya?ml)$/.test(relativePath) && !["LICENSE", ".npmrc"].includes(relativePath)) {
    continue;
  }
  const data = readFileSync(join(root, relativePath));
  if (data.includes(13)) {
    fail(`${relativePath} contains CR bytes; repository text files must use LF`);
  }
}

if (process.exitCode) {
  process.exit(process.exitCode);
}

console.log("PASS node-check");
