import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { once } from 'node:events';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
let server, base;
async function freePort() {
  const listener = createServer();
  listener.listen(0, '127.0.0.1');
  await once(listener, 'listening');
  const port = listener.address().port;
  listener.close();
  await once(listener, 'close');
  return port;
}
before(async () => {
  const port = await freePort();
  base = `http://127.0.0.1:${port}`;
  server = spawn(process.execPath, ['server.mjs'], {
    cwd: root, env: { ...process.env, PORT: String(port) }, stdio: ['ignore', 'pipe', 'pipe'],
  });
  let started = false;
  for (let attempt = 0; attempt < 40; attempt++) {
    if (server.exitCode !== null) throw new Error('Preview server exited prematurely');
    try {
      const response = await fetch(`${base}/manifest.json`);
      started = response.ok;
      if (started) break;
    } catch { /* Wait for the local listener. */ }
    await new Promise(r => setTimeout(r, 40));
  }
  assert.ok(started, 'Local preview should start promptly without dependencies');
});
after(() => { if (server && !server.killed) server.kill('SIGTERM'); });

test('root redirects to the frontend', async () => {
  const r = await fetch(base, { redirect: 'manual' });
  assert.equal(r.status, 302);
  assert.equal(r.headers.get('location'), '/frontend/index.html');
});
test('synthetic frontend is served with a non-sniffable HTML MIME type', async () => {
  const r = await fetch(`${base}/frontend/index.html`);
  assert.equal(r.status, 200);
  assert.match(r.headers.get('content-type'), /^text\/html/);
  assert.equal(r.headers.get('x-content-type-options'), 'nosniff');
  assert.match(await r.text(), /SYNTHETIC DEMO/);
});
test('every public-demo fixture is available locally', async () => {
  for (const file of ['/demo/index.html', '/demo-frames/demo-front-01.jpg',
                      '/demo-thumbs/demo-back-04.jpg', '/frontend/static/fixture-data.json']) {
    const r = await fetch(base + file);
    assert.equal(r.status, 200, file);
  }
});
test('SVG pipeline illustration is available without a network request', async () => {
  const r = await fetch(`${base}/docs/architecture-overview.svg`);
  assert.equal(r.status, 200);
  assert.match(r.headers.get('content-type'), /image\/svg\+xml/);
  assert.match(await r.text(), /Motion detected/);
});
test('HEAD is supported, write methods are rejected', async () => {
  assert.equal((await fetch(`${base}/frontend/index.html`, { method: 'HEAD' })).status, 200);
  assert.equal((await fetch(`${base}/frontend/index.html`, { method: 'POST' })).status, 405);
});
test('no private Git files, source files, or API are exposed', async () => {
  for (const name of ['/.git/config', '/server.mjs', '/README.md', '/package.json',
                      '/api/status', '/frontend/.env', '/frontend/%2e%2e/README.md']) {
    const r = await fetch(base + name);
    assert.equal(r.status, 404, name);
  }
});
test('only the reviewed synthetic asset paths are servable', async () => {
  const r = await fetch(`${base}/docs/PRIVATE_CONFIGURATION.md`);
  assert.equal(r.status, 404);
});
