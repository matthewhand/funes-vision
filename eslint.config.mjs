// ESLint flat config for the tests/*.js Node suites, the shared test loader
// (tests/helpers/load.cjs) and the SPA's inline <script> (extracted from
// index.html — see tools/lint/extract-inline.mjs).
//
// NPM-FREE: this file imports nothing and the repo has no package.json. It only
// needs an `eslint` binary. CI stays npm-free (no `npm install`), so JS linting
// is opt-in locally:
//
//   npx --yes eslint tests/ tools/                       # if eslint is available
//   node tools/lint/extract-inline.mjs \
//     | npx --yes eslint --stdin --stdin-filename index.inline.js
//
// Rules are correctness-only (no stylistic churn): dead code, typos, implicit
// globals, unreachable branches. Formatting is owned by .editorconfig.

const browserGlobals = {
  window: "readonly",
  document: "readonly",
  navigator: "readonly",
  location: "readonly",
  localStorage: "readonly",
  sessionStorage: "readonly",
  console: "readonly",
  fetch: "readonly",
  EventSource: "readonly",
  WebSocket: "readonly",
  IntersectionObserver: "readonly",
  MutationObserver: "readonly",
  ResizeObserver: "readonly",
  customElements: "readonly",
  HTMLElement: "readonly",
  HTMLImageElement: "readonly",
  Element: "readonly",
  Node: "readonly",
  Event: "readonly",
  Image: "readonly",
  URL: "readonly",
  URLSearchParams: "readonly",
  AbortController: "readonly",
  Response: "readonly",
  Headers: "readonly",
  TextEncoder: "readonly",
  TextDecoder: "readonly",
  requestAnimationFrame: "readonly",
  cancelAnimationFrame: "readonly",
  getComputedStyle: "readonly",
  matchMedia: "readonly",
  performance: "readonly",
  alert: "readonly",
  setTimeout: "readonly",
  clearTimeout: "readonly",
  setInterval: "readonly",
  clearInterval: "readonly",
};

const nodeGlobals = {
  require: "readonly",
  module: "writable",
  exports: "writable",
  __dirname: "readonly",
  __filename: "readonly",
  process: "readonly",
  console: "readonly",
  Buffer: "readonly",
  URL: "readonly",
  URLSearchParams: "readonly",
  setTimeout: "readonly",
  clearTimeout: "readonly",
  setInterval: "readonly",
  clearInterval: "readonly",
  queueMicrotask: "readonly",
};

const coreRules = {
  "no-undef": "error",
  "no-redeclare": "error",
  "no-dupe-keys": "error",
  "no-dupe-args": "error",
  "no-unreachable": "error",
  "no-constant-condition": "warn",
  "no-empty": "warn",
  "no-func-assign": "error",
  "no-unused-vars": ["warn", { args: "none", varsIgnorePattern: "^_" }],
  "use-isnan": "error",
  "valid-typeof": "error",
};

export default [
  {
    ignores: ["node_modules/**", "dist/**", "lucide.min.js", ".lint/**"],
  },
  {
    files: ["tests/**/*.js", "tests/**/*.cjs"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "commonjs",
      globals: { ...nodeGlobals, ...browserGlobals },
    },
    rules: coreRules,
  },
  {
    files: ["tools/**/*.js", "tools/**/*.mjs", "*.mjs"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: nodeGlobals,
    },
    rules: coreRules,
  },
  {
    files: ["index.inline.js", "**/*.inline.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "script",
      globals: browserGlobals,
    },
    rules: coreRules,
  },
];
