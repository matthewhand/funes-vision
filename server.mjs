/** Funes Vision synthetic preview server — dependency-free, no live cameras.
 *
 *  The static-file resolution and containment helpers are exported so the same
 *  code the server uses can be exercised against a temporary tree. Importing
 *  this module does not bind a port; only running it directly does.
 */
import { createServer } from 'node:http';
import { constants } from 'node:fs';
import { open, lstat, realpath } from 'node:fs/promises';
import { resolve, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const MAX_BYTES = 16 * 1024 * 1024;
const EXACT = new Set(['manifest.json', 'docs/architecture-overview.svg']);
const DIRECTORIES = ['frontend/', 'demo/', 'demo-frames/', 'demo-thumbs/', 'docs/evidence/'];
export const TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
  '.webp': 'image/webp', '.gif': 'image/gif', '.ico': 'image/x-icon',
  '.svg': 'image/svg+xml', '.webmanifest': 'application/manifest+json',
};

/**
 * Resolve a request path to a file, keeping it inside the directory the URL
 * selected. The decoded path is checked first, then every component below that
 * directory is refused when it is a symbolic link or a Windows junction or other
 * reparse point, and the fully resolved target must still sit inside the
 * resolved directory. No symlink is ever followed.
 *
 * @param {string} root already-resolved repository root
 * @param {string} raw   the raw request URL
 */
export async function resolveStaticFile(root, raw) {
  // Decode once and explicitly reject encoded separators, dot components,
  // NULs and Windows paths; only reviewed public-static directories are served.
  // The raw string is checked first because the WHATWG URL parser silently turns
  // backslashes into forward slashes for http(s).
  if (raw.includes('\\') || raw.includes('\0')) return null;
  let pathname;
  try { pathname = decodeURIComponent(new URL(raw, 'http://127.0.0.1').pathname); }
  catch { return null; }
  if (pathname.includes('\\') || pathname.includes('\0') || pathname.includes('//')) return null;
  const pieces = pathname.split('/');
  if (pieces.some(p => p === '.' || p === '..' || p.startsWith('.'))) return null;
  const relative = pieces.filter(Boolean).join('/');
  if (!relative) return null;
  let base;
  if (EXACT.has(relative)) base = root;
  else if (DIRECTORIES.some(prefix => relative.startsWith(prefix))) base = resolve(root, relative.split('/')[0]);
  else return null;
  const filename = resolve(root, relative);
  if (!filename.startsWith(base + sep)) return null;
  // The selected directory is the boundary, so it is checked as well as
  // everything below it: a junction can replace a reviewed prefix wholesale.
  for (let current = filename; current.length >= base.length; current = resolve(current, '..')) {
    try { if ((await lstat(current)).isSymbolicLink()) return null; }
    catch { return null; }
  }
  try {
    const real = await realpath(filename);
    const realBase = await realpath(base);
    if (!real.startsWith(realBase + sep)) return null;
  } catch { return null; }
  return {filename, base};
}

/** Serve through one handle, stat'ed through that same handle rather than the
 *  path, so a swapped file cannot be served under a stale name or size. */
export async function readContained(target, maxBytes = MAX_BYTES) {
  const handle = await open(target.filename, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const info = await handle.stat();
    if (!info.isFile() || info.size <= 0 || info.size > maxBytes) return null;
    const body = await handle.readFile();
    if (body.length !== info.size) return null;
    return {body, size: info.size};
  } finally {
    await handle.close();
  }
}

async function start() {
  const root = await realpath(fileURLToPath(new URL('.', import.meta.url)));
  const port = Number(process.env.PORT ?? 8000);
  // Bind only loopback on the host. Inside Docker, container networking requires
  // all interfaces; compose still publishes exclusively on host loopback.
  const host = process.env.FUNES_PREVIEW_CONTAINER === '1' ? '0.0.0.0' : '127.0.0.1';
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error('PORT must be a valid TCP port (1-65535)');
  }
  const server = createServer(async (req, res) => {
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Referrer-Policy', 'no-referrer');
    res.setHeader('Cache-Control', 'no-store');
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      res.writeHead(405, { Allow: 'GET, HEAD' }).end(); return;
    }
    if (req.url === '/' || req.url?.startsWith('/?')) {
      res.writeHead(302, { Location: '/frontend/index.html' }).end(); return;
    }
    const target = await resolveStaticFile(root, req.url ?? '');
    if (!target) { res.writeHead(404).end('Not found'); return; }
    const extension = target.filename.slice(target.filename.lastIndexOf('.'));
    const type = TYPES[extension];
    if (!type) { res.writeHead(404).end('Not found'); return; }
    let payload;
    try { payload = await readContained(target); }
    catch { res.writeHead(404).end('Not found'); return; }
    if (!payload) { res.writeHead(404).end('Not found'); return; }
    res.writeHead(200, { 'Content-Type': type, 'Content-Length': payload.size });
    res.end(req.method === 'HEAD' ? undefined : payload.body);
  });
  server.listen(port, host, () => {
    console.log(`Funes Vision synthetic preview: http://127.0.0.1:${port}/`);
  });
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  await start();
}