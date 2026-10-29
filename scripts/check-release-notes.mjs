// Check that the semantic-release plugins configured in .releaserc.json still work together.
//
// A dependency bump can pass every other check and still break the next release: a newer
// conventional-changelog preset needs conventional-changelog-writer >= 9, while the stable
// @semantic-release/release-notes-generator ships writer 8 ("Missing helper ..." at release
// time). This runs the real plugins on sample commits, so that failure shows up on the pull
// request that causes it. Needs `npm ci` first; reads .releaserc.json, never writes anything.
//
// Usage: node scripts/check-release-notes.mjs        (exit 0: ok, 1: a check failed)

import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const config = JSON.parse(await readFile(path.join(root, ".releaserc.json"), "utf8"));
const logger = { log() {}, info() {}, warn() {}, error() {}, success() {} };

const failures = [];
const fail = (label, error) => failures.push(`${label}: ${String(error?.message ?? error).split("\n")[0]}`);

function pluginOptions(name) {
  const entry = config.plugins.find((p) => (Array.isArray(p) ? p[0] : p) === name);
  if (!entry) throw new Error(`${name} is not listed in .releaserc.json`);
  return Array.isArray(entry) ? (entry[1] ?? {}) : {};
}

const commit = (message, n) => ({
  hash: String(n).repeat(40),
  message,
  subject: message.split("\n")[0],
  committerDate: new Date().toISOString(),
  commit: { short: String(n).repeat(7) },
});

// The release policy this project relies on: what each commit type does to the version.
// If .releaserc.json changes the policy on purpose, change this table with it.
const expected = [
  ["feat: add a thing", "minor"],
  ["fix: repair a thing", "patch"],
  ["perf: speed up a thing", "patch"],
  ["refactor: restructure a thing", "patch"],
  ["fix!: change a contract\n\nBREAKING CHANGE: the contract changed", "minor"],
  ["docs: describe a thing", null],
  ["test: cover a thing", null],
  ["chore: tidy a thing", null],
  ["ci: adjust a workflow", null],
];

try {
  const { analyzeCommits } = await import("@semantic-release/commit-analyzer");
  const options = pluginOptions("@semantic-release/commit-analyzer");
  for (const [message, release] of expected) {
    try {
      const got = (await analyzeCommits(options, { cwd: root, commits: [commit(message, 1)], logger })) ?? null;
      if (got !== release) fail(`commit-analyzer on "${message.split("\n")[0]}"`, `expected ${release}, got ${got}`);
    } catch (error) {
      fail(`commit-analyzer on "${message.split("\n")[0]}"`, error);
    }
  }
} catch (error) {
  fail("commit-analyzer", error);
}

try {
  const { generateNotes } = await import("@semantic-release/release-notes-generator");
  const notes = await generateNotes(pluginOptions("@semantic-release/release-notes-generator"), {
    cwd: root,
    commits: [commit("fix: repair a thing", 1), commit("feat: add a thing", 2)],
    lastRelease: { gitTag: "v0.0.1", version: "0.0.1" },
    nextRelease: { gitTag: "v0.1.0", version: "0.1.0", gitHead: "3".repeat(40) },
    options: { repositoryUrl: "https://github.com/example/example.git" },
    logger,
  });
  for (const subject of ["repair a thing", "add a thing"]) {
    if (!notes.includes(subject)) fail("release-notes-generator", `the notes do not mention "${subject}"`);
  }
} catch (error) {
  fail("release-notes-generator", error);
}

if (failures.length) {
  console.error("Release tooling check FAILED:");
  for (const line of failures) console.error(`  - ${line}`);
  console.error(
    "A recent dependency change probably made the configured plugins incompatible; see the dependency section of docs/Operations Runbook.md.",
  );
  process.exit(1);
}
console.log(`Release tooling check passed (${expected.length} commit cases, release notes rendered).`);
