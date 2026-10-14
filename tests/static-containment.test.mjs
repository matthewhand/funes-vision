/** Containment proof for the Node static preview resolver, run against a
 *  temporary tree. No repository file, camera folder or network is touched. */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, symlink, lstat, rm } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { realpath } from 'node:fs/promises';
import { resolveStaticFile, readContained } from '../server.mjs';

const run = promisify(execFile);
let root, outside;

before(async () => {
  root = await realpath(await mkdtemp(join(tmpdir(), 'funes-static-root-')));
  outside = await realpath(await mkdtemp(join(tmpdir(), 'funes-static-outside-')));
  await mkdir(join(root, 'frontend/static'), {recursive: true});
  await mkdir(join(root, 'demo-frames'), {recursive: true});
  await mkdir(join(root, 'docs/evidence'), {recursive: true});
  await writeFile(join(root, 'frontend/index.html'), '<h1>SYNTHETIC DEMO</h1>');
  await writeFile(join(root, 'frontend/static/app.js'), 'export const x = 1;');
  await writeFile(join(root, 'demo-frames/frame.jpg'), '\xff\xd8\xff\xd9');
  await writeFile(join(root, 'docs/evidence/shot.png'), 'png');
  await writeFile(join(root, 'manifest.json'), '{"status":"synthetic"}');
  await writeFile(join(root, 'secret.txt'), 'private');
  await writeFile(join(outside, 'secret.txt'), 'outside');
});

after(async () => {
  await rm(root, {recursive: true, force: true});
  await rm(outside, {recursive: true, force: true});
});

test('reviewed static files inside their own directory are served', async () => {
  for (const url of ['/frontend/index.html', '/frontend/static/app.js', '/demo-frames/frame.jpg',
                     '/docs/evidence/shot.png', '/manifest.json']) {
    const target = await resolveStaticFile(root, url);
    assert.ok(target, url);
    const payload = await readContained(target);
    assert.ok(payload && payload.size > 0, url);
  }
});

test('only the reviewed prefixes are servable', async () => {
  for (const url of ['/secret.txt', '/package.json', '/server.mjs', '/tools/seed_synthetic_watch.py',
                     '/frontend/../secret.txt', '/docs/PRIVATE_CONFIGURATION.md', '/api/activity',
                     '/frontend/%2e%2e/secret.txt', '/frontend//index.html', '/frontend\\index.html']) {
    assert.equal(await resolveStaticFile(root, url), null, url);
  }
});

test('a symlinked file is never followed', async t => {
  try {
    await symlink(join(outside, 'secret.txt'), join(root, 'frontend/linked.txt'));
  } catch (error) {
    t.skip(`symlink creation refused: ${error.code}`);
    return;
  }
  assert.equal(await resolveStaticFile(root, '/frontend/linked.txt'), null);
  assert.equal((await resolveStaticFile(root, '/frontend/index.html'))?.filename,
    join(root, 'frontend/index.html'));
});

test('a symlinked parent directory is never followed', async t => {
  try {
    await symlink(outside, join(root, 'demo-linked'), 'junction');
  } catch (error) {
    t.skip(`symlink creation refused: ${error.code}`);
    return;
  }
  // The link is not a reviewed prefix, so the URL never selects it at all.
  assert.equal(await resolveStaticFile(root, '/demo-linked/secret.txt'), null);
  // A reviewed prefix that has itself been replaced by a link is refused too.
  const demo = join(root, 'demo-frames');
  await rm(demo, {recursive: true, force: true});
  await symlink(outside, demo, 'junction');
  assert.equal(await resolveStaticFile(root, '/demo-frames/secret.txt'), null);
});

test('a Windows directory junction is never followed', async t => {
  if (process.platform !== 'win32') {
    t.skip('POSIX symlink coverage applies here');
    return;
  }
  const evidence = join(root, 'docs/evidence');
  await rm(evidence, {recursive: true, force: true});
  try {
    await run('cmd', ['/c', 'mklink', '/J', evidence, outside]);
  } catch (error) {
    t.skip(`junction creation refused: ${error.code || error.message}`);
    return;
  }
  const info = await lstat(evidence);
  assert.ok(info.isSymbolicLink(), 'a junction must be observable as a link');
  assert.equal(await resolveStaticFile(root, '/docs/evidence/secret.txt'), null);
});

test('a directory is never served as a file', async () => {
  const target = await resolveStaticFile(root, '/frontend/static');
  assert.equal(await readContained(target), null);
});