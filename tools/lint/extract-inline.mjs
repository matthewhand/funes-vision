#!/usr/bin/env node
// Extract the SPA's inline <script> bodies (those without a src=) from
// index.html and write them to stdout, so ESLint can check them without an
// npm toolchain:
//
//   node tools/lint/extract-inline.mjs \
//     | npx --yes eslint --stdin --stdin-filename index.inline.js
//
// Uses only Node core modules — no dependencies, no package.json.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const html = readFileSync(join(repoRoot, "index.html"), "utf8");

const inlineScript = /<script\b(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi;

let out = "";
for (const match of html.matchAll(inlineScript)) {
  out += match[1] + "\n";
}

process.stdout.write(out);
