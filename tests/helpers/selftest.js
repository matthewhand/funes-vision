// Proves tests/helpers/load.cjs fails LOUDLY. A loader that answers a miss with
// '' or null is worse than no loader: every `assert.ok(!/x/.test(rule('.gone')))`
// written against it is green forever, and renaming things is exactly what the
// index.html -> modules split does. So this suite asserts, for every lookup,
// that a miss throws an error naming what was missing AND the document it was
// missing from -- and it proves grabFn's lexer by feeding it a function whose
// body has braces inside a string, a template literal, a regex literal and both
// kinds of comment.
//
// Everything here runs against a SYNTHETIC fixture (createLoader over a string),
// never against index.html, so the self-test keeps working -- and keeps meaning
// something -- after the app is split and renamed.
//
// Run directly: node tests/helpers/selftest.js
// Counted by tests/run.sh via tests/test_helpers_selftest.js.
const assert = require('assert');
const { Script } = require('node:vm');
const { createLoader } = require('./load.cjs');

const FIXTURE = `<!doctype html>
<html>
<head>
  <meta name="theme-color" content="#080c14">
  <meta property="og:image" content="icon.svg">
  <link rel="manifest" href="manifest.json">
</head>
<body>
  <div id="alpha" class="panel"></div>
  <div id="beta"></div>
  <style>
    /* A rule that exists only inside a comment:
       .commented { color: var(--accent); }
    */
    .wrap .panel { color: var(--muted); }
    .panel { color: var(--ink); padding: 4px; }
    .panel, .toast { border: 1px solid var(--line); }
    .toast { background: var(--surface); }
    .toast i { color: var(--accent); }
    @media (max-width: 360px) {
      .panel { display: none; }
    }
  </style>
  <script>
    // === pure:widgetLabel ===
    function widgetLabel(n) {
      return n + ' widget' + (n === 1 ? '' : 's');
    }
    // === /pure:widgetLabel ===

    // === pure:empty ===

    // === /pure:empty ===

    function scanner(x) {
      var inString = '}' + '}}';
      var inTemplate = \`a\${ { k: 'v' }.k }b\`;
      var inRegex = /[{}]/.test(x) ? 'yes' : 'no';
      var inDivision = x / 2 / '}';
      /* a block comment with a } and a { in it */
      // and a line comment with a }
      if (inString && inTemplate && inRegex && inDivision) {
        return function inner() { return { ok: true }; };
      }
      return null;
    }

    async function fetched(url) {
      const r = await fetch(url);
      return r.json();
    }
  </script>
</body>
</html>`;

const L = createLoader(FIXTURE, '<fixture>');

function throwsOn(what, fn, ...expect) {
  let threw = null;
  let value;
  try {
    value = fn();
  } catch (e) {
    threw = e;
  }
  assert.ok(threw, what + ': expected a throw, got ' + JSON.stringify(value));
  assert.ok(threw instanceof Error, what + ': expected an Error, got ' + threw);
  for (const needle of expect) {
    assert.ok(threw.message.includes(needle),
      what + ': the error must name ' + JSON.stringify(needle) + ', got: ' + threw.message);
  }
  return threw;
}

// ---- the happy path, so the throws below mean something -------------------

assert.strictEqual(L.rule('.panel'), ' color: var(--ink); padding: 4px; ');
assert.ok(L.rule('.panel').indexOf('var(--muted)') === -1,
  'rule() matches a selector EXACTLY: ".wrap .panel" contains ".panel", and a ' +
  'substring match would hand back the wrong rule body');
assert.strictEqual(L.decl(L.rule('.wrap .panel'), 'color'), 'var(--muted)',
  'the compound selector is still reachable, under its own name');
assert.strictEqual(L.decl(L.rule('.toast'), 'border'), '1px solid var(--line)',
  'a comma-separated selector list is reachable under either selector');
assert.strictEqual(L.ruleN('.toast', 1), ' background: var(--surface); ',
  'ruleN() is the nth match, and rule() is the first');
assert.strictEqual(L.ruleN('.panel', 2), ' display: none; ',
  'a rule nested in @media registers under its own selector, at its own position in the sheet');
assert.strictEqual(L.rules('.panel').length, 3);
assert.strictEqual(L.decl(L.rule('.panel'), 'padding'), '4px');
assert.ok(L.rule('.toast i').includes('var(--accent)'),
  'a compound selector is its own key, never a substring hit of its parent');
assert.ok(!L.css().includes('/*'), 'css() strips comments');
assert.ok(L.css().includes('.panel'), 'css() keeps the real rules');
assert.ok(L.markup().includes('<div id="alpha"') && !L.markup().includes('<head>'));
assert.ok(L.head().includes('theme-color') && !L.head().includes('<div id="alpha"'));
assert.strictEqual(L.meta('theme-color'), '#080c14');
assert.strictEqual(L.meta('og:image'), 'icon.svg');
assert.deepStrictEqual(L.ids(), ['alpha', 'beta']);
assert.strictEqual(L.hasId('alpha'), true);
assert.strictEqual(L.hasId('gamma'), false);
assert.strictEqual(L.mediaBodies(/max-width:\s*360px/).length, 1);
assert.strictEqual(L.mediaBodies(/max-width:\s*360px/)[0], '\n      .panel { display: none; }\n    ');
assert.ok(L.appSource().includes('function scanner'));
assert.ok(!L.appSource().includes('src='), 'a <script src=...> is external, not inline');
assert.strictEqual(L.loadSentinel('widgetLabel').widgetLabel,
  FIXTURE.match(/pure:widgetLabel ===\n([\s\S]*?)\n\s*\/\/ === \/pure:widgetLabel/)[1],
  'loadSentinel() returns the text between the marker pair');
assert.ok(new Script(L.loadSentinel('widgetLabel').widgetLabel),
  'and that text is runnable on its own');

// ---- every lookup must throw on a miss, not answer with an empty value -----

throwsOn('rule() on a missing selector', () => L.rule('.nope'),
  'a CSS rule for selector ".nope"', '<fixture>');
throwsOn('rule() on a selector that only exists inside a CSS comment',
  () => L.rule('.commented'),
  'a CSS rule for selector ".commented"', '<fixture>');
throwsOn('rule() on a substring of a real selector, not a selector',
  () => L.rule('.toast i .nope'),
  'a CSS rule for selector ".toast i .nope"');
throwsOn('rules() on a missing selector', () => L.rules('.nope'),
  'a CSS rule for selector ".nope"', '<fixture>');
throwsOn('ruleN() past the last match', () => L.ruleN('.panel', 9),
  'CSS rule #9 for selector ".panel"', '3 matches', '<fixture>');
throwsOn('ruleN() with a negative index', () => L.ruleN('.panel', -1),
  'CSS rule #-1 for selector ".panel"', '<fixture>');
throwsOn('ruleN() with a non-integer index', () => L.ruleN('.panel', 1.5),
  'CSS rule #1.5 for selector ".panel"', '<fixture>');
throwsOn('decl() on a property the rule does not set',
  () => L.decl(L.rule('.panel'), 'colour'),
  'a "colour" declaration', 'body was:', 'color: var(--ink); padding: 4px;', '<fixture>');
throwsOn('grabFn() on a function that does not exist', () => L.grabFn('notAFunction'),
  'the source of function notAFunction()', '<fixture>');
throwsOn('loadSentinel() on a sentinel that does not exist', () => L.loadSentinel('notASentinel'),
  'the "pure:notASentinel" sentinel block', '<fixture>');
throwsOn('loadSentinel() on a sentinel whose body is empty', () => L.loadSentinel('empty'),
  'an empty "pure:empty" sentinel body', '<fixture>');
throwsOn('meta() on a tag the document does not have', () => L.meta('description'),
  'a <meta name|property="description"> tag', '<fixture>');

// hasId() answers a question rather than looking something up, so it must NOT
// throw -- but the caller still has to be able to see an empty document.
assert.strictEqual(L.hasId('gamma'), false);
assert.deepStrictEqual(createLoader('<html><head></head><body></body></html>', '<bare>').ids(), [],
  'ids() is a list, so an empty document shows up as [] rather than as a silent pass');

// ---- every structural lookup must throw on a document that has no such part -

throwsOn('css() with no <style>', () => createLoader('<p>hi</p>', '<no-style>').css(),
  'a <style> block', '<no-style>');
throwsOn('markup() with no <body>', () => createLoader('<p>hi</p>', '<no-body>').markup(),
  'a <body> element', '<no-body>');
throwsOn('head() with no <head>', () => createLoader('<p>hi</p>', '<no-head>').head(),
  'a <head> element', '<no-head>');
throwsOn('appSource() when every <script> is external',
  () => createLoader('<body><script src="a.js"></script></body>', '<external>').appSource(),
  'an inline <script> body', '<external>');
throwsOn('mediaBodies() on an @media that never closes',
  () => createLoader('<style>@media (max-width: 5px) { .a { b: c; }</style>', '<open>').mediaBodies(/max-width/),
  'the closing brace of an @media block', '<open>');
throwsOn('grabFn() whose body never closes',
  () => createLoader('<script>\nfunction broken() {\n  if (1) {\n</script>', '<unbalanced>').grabFn('broken'),
  'the closing brace of function broken()', 'braces do not balance', '<unbalanced>');

// ---- grabFn: the reason it is a lexer and not an indexOf('}') --------------

const scanner = L.grabFn('scanner');
for (const [what, needle] of [
  ['a } inside a string literal', "var inString = '}' + '}}';"],
  ['braces inside a template literal and its ${} interpolation', "var inTemplate = `a${ { k: 'v' }.k }b`;"],
  ['braces inside a regex character class', 'var inRegex = /[{}]/.test(x)'],
  ['a division (so a / is not mistaken for a regex)', "var inDivision = x / 2 / '}';"],
  ['braces inside a block comment', '/* a block comment with a } and a { in it */'],
  ['a } inside a line comment', '// and a line comment with a }'],
  ['the nested function at the end of the body', 'return function inner() { return { ok: true }; };'],
]) {
  assert.ok(scanner.includes(needle), 'grabFn() must survive ' + what + ' -- it returned:\n' + scanner);
}
assert.strictEqual(scanner, scanner.slice(0, scanner.lastIndexOf('}') + 1),
  'grabFn() must return through the matching brace: no more, no less');
assert.ok(!scanner.includes('</script>'), 'grabFn() must not run past the function into the page');
assert.ok(new Script(scanner), 'the extracted source must parse on its own');

// This is the failure mode being prevented. The depth counter that shipped in
// four suites stops at the first `}`, so the body it hands the assertions is a
// fragment that does not even parse -- and every regex run over it was reading
// the wrong text while still reporting PASS.
const naive = FIXTURE.slice(FIXTURE.indexOf('function scanner'), FIXTURE.indexOf('function scanner') + 200);
const naiveBody = naive.slice(0, naive.indexOf('}') + 1);
assert.ok(naiveBody.length < scanner.length,
  'the naive depth counter truncates scanner(); grabFn() must not');
assert.throws(() => new Script(naiveBody), SyntaxError,
  'the truncated body must not parse -- that is why grabFn() has a lexer');

// Destructured and defaulted parameters: the `{` in the signature is not the body.
const params = createLoader(
  '<script>\nfunction withParams({ a, b }, c = { d: 1 }) {\n  return a + b + c.d;\n}\n</script>',
  '<params>'
).grabFn('withParams');
assert.strictEqual(params, 'function withParams({ a, b }, c = { d: 1 }) {\n  return a + b + c.d;\n}');
assert.ok(new Script(params), 'a destructured signature must not truncate the body');

const fetched = L.grabFn('fetched');
assert.ok(fetched.startsWith('async function fetched(url) {'), 'async functions are found too');
assert.ok(new Script(fetched), 'and their body is returned whole');
assert.ok(!L.grabFn('widgetLabel').includes('scanner'),
  'grabFn() returns one function, not its neighbours');

console.log('selftest: loader throws on every miss; grabFn survives strings, templates, regex and comments');
