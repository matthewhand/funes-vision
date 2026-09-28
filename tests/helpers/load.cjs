'use strict';

// Shared loader for the Node suites in tests/*.js.
//
// index.html is the app's only source of truth, and ~90 suites assert on it as
// TEXT: a CSS rule body, a markup fragment, a function body, a sentinel block.
// Before this module every suite rolled its own `fs.readFileSync` + a private
// regex scraper, in three slightly different flavours, and half of them returned
// '' on a miss -- so a selector that stopped existing quietly turned a real
// assertion into `assert.ok(!false)`, which is green. That is the single worst
// failure mode a text-scraping test suite has, and it is the reason this file
// exists: EVERY lookup below THROWS, naming what was not found and which file it
// was not found in. tests/helpers/selftest.js proves each one actually throws.
//
// Zero dependencies, CommonJS, `require`-only, and it reads the app the way the
// tests have always read it: one read of index.html per process, byte-for-byte.
//
// Semantics that callers depend on (a later source split must not change them):
//   * `rule()` matches a selector EXACTLY, after collapsing whitespace and
//     splitting comma-separated selector lists. It does not substring-match, so
//     `rule('.toast')` can never return `.toast i`'s body.
//   * `css()` strips /* ... */ comments, because a CSS comment in this page
//     carries the "why" for ~90 rules and a comment that happened to contain
//     `color: var(--accent)` would otherwise read as a real declaration.
//   * `grabFn()` walks braces with a real JS lexer, so a `}` inside a string, a
//     template literal, a regex literal or a comment does not truncate it.
//   * `appSource()` is byte-identical to `node tools/lint/extract-inline.mjs`,
//     so the suites and the ESLint / `node --check` gate see the same script.
//
// `createLoader(source, label)` is the same API over any document, which is how
// tests/helpers/selftest.js can prove the failure modes without reading the app.

const fs = require('fs');
const path = require('path');

const CSS_COMMENT = /\/\*[\s\S]*?\*\//g;

function createLoader(src, label) {
  function fail(what) {
    throw new Error(
      what + ' -- not found in ' + label + '. A suite that found nothing here ' +
      'would go on to assert against an empty string and pass, so this is a hard failure.'
    );
  }

  // ---- CSS ----------------------------------------------------------------

  // The document's CSS: every <style> block joined, comments stripped.
  function css() {
    const blocks = [...src.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)].map((m) => m[1]);
    if (!blocks.length) fail('a <style> block');
    return blocks.join('\n').replace(CSS_COMMENT, '');
  }

  let ruleCache = null;

  // Every `selector { body }` pair, in source order. A comma-separated selector
  // list registers once per selector, so `.visit-meta, .visit-caption { ... }`
  // is reachable as either. A rule nested in an @media block registers under its
  // own selector -- the at-rule prelude is not part of a selector, so
  // `@media (max-width: 768px)` is never something you look up.
  function cssRules() {
    if (!ruleCache) {
      ruleCache = [];
      for (const m of css().matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
        for (const part of m[1].split(',')) {
          const selector = part.trim().replace(/\s+/g, ' ');
          if (selector) ruleCache.push({ selector, body: m[2] });
        }
      }
    }
    return ruleCache;
  }

  function matches(selector) {
    const want = String(selector).trim().replace(/\s+/g, ' ');
    return cssRules().filter((r) => r.selector === want);
  }

  // Every rule body for `selector`, in source order. Throws when there is none,
  // because `.some(...)` over an empty array is a silently-passing assertion.
  function rules(selector) {
    const found = matches(selector);
    if (!found.length) fail('a CSS rule for selector "' + selector + '"');
    return found.map((r) => r.body);
  }

  // The FIRST rule body for `selector`. Throws when there is none.
  function rule(selector) {
    return rules(selector)[0];
  }

  // The nth (0-based) rule body for `selector`. Throws when there is no such
  // match, which is how "this rule moved into a media query" gets reported
  // instead of silently reading the first hit.
  function ruleN(selector, n) {
    const found = matches(selector);
    if (!Number.isInteger(n) || n < 0 || n >= found.length) {
      fail('CSS rule #' + n + ' for selector "' + selector + '" (' + found.length + ' match' +
        (found.length === 1 ? '' : 'es') + ')');
    }
    return found[n].body;
  }

  // The value of `prop` in a rule body: decl(rule('.toast'), 'border') ->
  // '1px solid var(--border-color)'. Throws when the property is not declared,
  // so a renamed declaration fails instead of reading as an empty string.
  function decl(body, prop) {
    const m = String(body).match(new RegExp('(?:^|[;{\\s])' + prop + '\\s*:\\s*([^;]+);'));
    if (!m) {
      fail('a "' + prop + '" declaration (body was: ' +
        String(body).trim().replace(/\s+/g, ' ').slice(0, 120) + ')');
    }
    return m[1].trim();
  }

  // The body of every `@media` block whose prelude matches `preludeRe`, with real
  // brace balancing rather than a regex up to the next `}`, so a nested rule or
  // a comment cannot truncate the window a caller is asserting on.
  function mediaBodies(preludeRe) {
    const sheet = css();
    const out = [];
    const re = /@media([^{]+)\{/g;
    let m;
    while ((m = re.exec(sheet)) !== null) {
      if (!preludeRe.test(m[1])) continue;
      const end = balancedEnd(sheet, m.index + m[0].length - 1);
      if (end === -1) fail('the closing brace of an @media block matching ' + preludeRe);
      out.push(sheet.slice(m.index + m[0].length, end - 1));
      re.lastIndex = end;
    }
    return out;
  }

  // ---- markup -------------------------------------------------------------

  // The <body> element's inner HTML, verbatim (comments included -- strip them
  // yourself when a comment must not be read as markup).
  function markup() {
    const open = src.match(/<body\b[^>]*>/i);
    if (!open) fail('a <body> element');
    const at = open.index + open[0].length;
    const close = src.indexOf('</body>', at);
    if (close === -1) fail('a </body> element');
    return src.slice(at, close);
  }

  // Everything before <body>: the head, where title/meta/link live.
  function head() {
    const at = src.search(/<head\b[^>]*>/i);
    if (at === -1) fail('a <head> element');
    const inner = at + src.slice(at).match(/<head\b[^>]*>/i)[0].length;
    const close = src.indexOf('</head>', inner);
    return src.slice(inner, close === -1 ? src.length : close);
  }

  // The `content` of a <meta name="..."> or <meta property="..."> tag. Throws
  // when the tag is absent, so `meta('og:image')` cannot read as undefined and
  // slip past a `!/^https?:/.test(img)` check.
  function meta(name) {
    const m = src.match(new RegExp(
      '<meta\\b[^>]*(?:name|property)=["\']' + name + '["\'][^>]*content=["\']([^"\']*)["\']'
    )) || src.match(new RegExp(
      '<meta\\b[^>]*content=["\']([^"\']*)["\'][^>]*(?:name|property)=["\']' + name + '["\']'
    ));
    if (!m) fail('a <meta name|property="' + name + '"> tag');
    return m[1];
  }

  let idCache = null;

  // Every `id="..."` in the document, including ids that only appear inside the
  // inline script's template strings. A superset on purpose: a getElementById
  // target built in a template still has to resolve.
  function ids() {
    if (!idCache) {
      idCache = [];
      const seen = new Set();
      for (const m of src.matchAll(/\bid=["']([^"']+)["']/g)) {
        if (!seen.has(m[1])) { seen.add(m[1]); idCache.push(m[1]); }
      }
    }
    return idCache;
  }

  function hasId(id) {
    return new RegExp('\\bid=["\']' + id + '["\']').test(src);
  }

  // ---- the inline script -------------------------------------------------

  // The inline <script> bodies (those with no src=) -- byte-identical to
  // `node tools/lint/extract-inline.mjs`, which is what ESLint and tests/run.sh's
  // `node --check` gate parse. Throws when there is no inline script, because
  // every "this banned pattern is ABSENT from the app source" assertion goes
  // vacuous on an empty string.
  function appSource() {
    const blocks = [...src.matchAll(/<script\b(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)];
    if (!blocks.length) fail('an inline <script> body');
    // Appended, not joined: the tool writes `match[1] + "\n"` per script, so the
    // last one is newline-terminated too.
    return blocks.map((m) => m[1] + '\n').join('');
  }

  // ---- a JS lexer good enough to find a function's closing brace -----------

  // Words after which a `/` opens a regex literal rather than dividing. After any
  // other identifier the `/` divides.
  const REGEX_AFTER_WORD = new Set([
    'return', 'typeof', 'instanceof', 'in', 'of', 'new', 'delete', 'void',
    'throw', 'case', 'do', 'else', 'yield', 'await',
  ]);
  // A `/` after one of these divides. `)` `]` and `}` are the classic ambiguity
  // (`if (x) /re/.test(y)`); treating them as division is the only choice that is
  // right for ordinary code.
  const DIVIDES_AFTER = new Set([')', ']', '}', '++', '--']);

  function regexAllowed(prev) {
    if (!prev) return true;
    if (prev.t === 'word') return REGEX_AFTER_WORD.has(prev.v);
    if (prev.t === 'num' || prev.t === 'str' || prev.t === 're') return false;
    if (prev.t === 'punct') return !DIVIDES_AFTER.has(prev.v);
    return true;
  }

  function endOfQuoted(text, at) {
    const quote = text[at];
    for (let i = at + 1; i < text.length; i++) {
      if (text[i] === '\\') { i++; continue; }
      if (text[i] === quote) return i + 1;
      if (text[i] === '\n') return i; // unterminated: a newline ends a ' or " string
    }
    return text.length;
  }

  function endOfRegex(text, at) {
    let inClass = false;
    for (let i = at + 1; i < text.length; i++) {
      const c = text[i];
      if (c === '\\') { i++; continue; }
      if (c === '\n') return i; // not a regex after all
      if (inClass) { if (c === ']') inClass = false; continue; }
      if (c === '[') { inClass = true; continue; }
      if (c === '/') {
        let j = i + 1;
        while (j < text.length && /[a-z]/.test(text[j])) j++; // flags
        return j;
      }
    }
    return text.length;
  }

  // Index just past a backtick string starting at `at`, honouring `${}` nesting,
  // so the parameter-list scan below is not fooled by a default value.
  function skipTemplate(text, at) {
    const open = [0];
    for (let i = at + 1; i < text.length; i++) {
      const c = text[i];
      if (c === '\\') { i++; continue; }
      if (c === '`') { if (open.length === 1) return i + 1; continue; }
      if (c === '$' && text[i + 1] === '{') { open.push(0); i++; continue; }
      if (c === '}' && open.length > 1) open.pop();
    }
    return text.length;
  }

  // The index just past the `}` that closes the `{` at `openIdx`, or -1. Walks
  // the code with a real lexer, so braces inside strings, template literals,
  // regex literals and comments are skipped rather than counted.
  function balancedEnd(text, openIdx) {
    if (text[openIdx] !== '{') return -1;
    // One frame per open template interpolation, so a `}` can close either a code
    // block or a `${`, and the right context resumes afterwards.
    const frames = [{ code: true, depth: 0 }];
    let prev = null; // last significant token: {t, v}
    for (let i = openIdx; i < text.length; i++) {
      const top = frames[frames.length - 1];
      const c = text[i];

      if (!top.code) {
        if (c === '\\') { i++; continue; }
        if (c === '`') { frames.pop(); prev = { t: 'str' }; continue; }
        if (c === '$' && text[i + 1] === '{') { frames.push({ code: true, depth: 1 }); prev = null; i++; }
        continue;
      }

      if (c === '/' && text[i + 1] === '/') {
        const nl = text.indexOf('\n', i);
        i = nl === -1 ? text.length : nl;
        continue;
      }
      if (c === '/' && text[i + 1] === '*') {
        const e = text.indexOf('*/', i + 2);
        i = e === -1 ? text.length : e + 1;
        continue;
      }
      if (c === "'" || c === '"') { i = endOfQuoted(text, i) - 1; prev = { t: 'str' }; continue; }
      if (c === '`') { frames.push({ code: false, depth: 0 }); continue; }
      if (c === '/' && regexAllowed(prev)) { i = endOfRegex(text, i) - 1; prev = { t: 're' }; continue; }
      if (c === '{') { top.depth++; prev = { t: 'punct', v: '{' }; continue; }
      if (c === '}') {
        top.depth--;
        if (top.depth < 0) return -1;
        prev = { t: 'punct', v: '}' };
        if (top.depth === 0) {
          if (frames.length === 1) return i + 1; // closed the brace we opened on
          frames.pop();
        }
        continue;
      }
      if (/[A-Za-z_$]/.test(c)) {
        let j = i;
        while (j < text.length && /[A-Za-z0-9_$]/.test(text[j])) j++;
        prev = { t: 'word', v: text.slice(i, j) };
        i = j - 1;
        continue;
      }
      if (/[0-9]/.test(c)) {
        let j = i;
        while (j < text.length && /[0-9a-zA-Z_.]/.test(text[j])) j++;
        prev = { t: 'num' };
        i = j - 1;
        continue;
      }
      if (/\s/.test(c)) continue;
      prev = { t: 'punct', v: c };
    }
    return -1;
  }

  // The full source of a top-level `function NAME(...) { ... }` (or
  // `async function NAME(...)`), from the `function` keyword through the closing
  // brace. Throws when there is no such function, or when the braces do not
  // balance. Destructured and defaulted parameters are handled, so a `{` in the
  // signature is never mistaken for the body.
  function grabFn(name) {
    const at = new RegExp('\\n([ \\t]*)(?:async )?function ' + name + '\\s*\\(').exec(src);
    if (!at) fail('the source of function ' + name + '()');
    const start = at.index + 1 + at[1].length;
    const paren = src.indexOf('(', at.index);
    let depth = 0;
    let close = -1;
    for (let i = paren; i < src.length; i++) {
      const c = src[i];
      if (c === "'" || c === '"') { i = endOfQuoted(src, i) - 1; continue; }
      if (c === '`') { i = skipTemplate(src, i) - 1; continue; }
      if (c === '(') depth++;
      else if (c === ')' && --depth === 0) { close = i; break; }
    }
    if (close === -1) fail('the end of the parameter list of function ' + name + '()');
    const open = src.indexOf('{', close);
    if (open === -1) fail('the body of function ' + name + '()');
    const end = balancedEnd(src, open);
    if (end === -1) fail('the closing brace of function ' + name + '() -- braces do not balance');
    return src.slice(start, end);
  }

  // ---- sentinels ----------------------------------------------------------

  // The source between each `// === pure:NAME ===` / `// === /pure:NAME ===`
  // marker pair, keyed by name, ready to `eval`. Transitional: it exists so the
  // sentinel-based suites can move off their private regex one at a time, and it
  // disappears when the helpers are real ES modules. Throws when a marker pair is
  // missing or the body is empty -- an empty body means the `eval` below defines
  // nothing and every assertion fails with "X is not a function", which reads
  // like a broken test rather than a broken page.
  function loadSentinel(...names) {
    const out = {};
    for (const name of names) {
      const m = src.match(new RegExp(
        '// === pure:' + name + ' ===\\n([\\s\\S]*?)\\n\\s*// === /pure:' + name
      ));
      if (!m) fail('the "pure:' + name + '" sentinel block (both markers)');
      if (!m[1].trim()) fail('an empty "pure:' + name + '" sentinel body');
      out[name] = m[1];
    }
    return out;
  }

  return {
    src,
    css,
    cssRules,
    rules,
    rule,
    ruleN,
    decl,
    mediaBodies,
    markup,
    head,
    meta,
    ids,
    hasId,
    appSource,
    grabFn,
    loadSentinel,
  };
}

const file = path.join(__dirname, '..', '..', 'index.html');

module.exports = Object.assign(
  createLoader(fs.readFileSync(file, 'utf8'), file),
  { createLoader, file }
);
