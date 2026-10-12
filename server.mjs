/** Funes Vision synthetic preview server — dependency-free, no live cameras. */
import { createServer } from 'node:http';
import { createReadStream } from 'node:fs';
import { stat } from 'node:fs/promises';
import { resolve, join, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(fileURLToPath(new URL('.', import.meta.url)));
const port = Number(process.env.PORT ?? 8000);
// Bind only loopback on the host. Inside Docker, container networking requires
// all interfaces; compose still publishes exclusively on host loopback.
const host = process.env.FUNES_PREVIEW_CONTAINER === '1' ? '0.0.0.0' : '127.0.0.1';
if (!Number.isInteger(port) || port < 1 || port > 65535) {
  throw new Error('PORT must be a valid TCP port (1-65535)');
}
const exact = new Set(['manifest.json', 'docs/architecture-overview.svg']);
const directories = ['frontend/', 'demo/', 'demo-frames/', 'demo-thumbs/', 'docs/evidence/'];
const types = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
  '.webp': 'image/webp', '.gif': 'image/gif', '.ico': 'image/x-icon',
  '.svg': 'image/svg+xml', '.webmanifest': 'application/manifest+json',
};
function filenameFor(raw) {
  // Decode once and explicitly reject encoded separators, dot components,
  // NULs and Windows paths; only reviewed public-static directories are served.
  let pathname;
  try { pathname = decodeURIComponent(new URL(raw, 'http://127.0.0.1').pathname); }
  catch { return null; }
  if (pathname.includes('\\') || pathname.includes('\0') || pathname.includes('//')) return null;
  const pieces = pathname.split('/');
  if (pieces.some(p => p === '.' || p === '..' || p.startsWith('.'))) return null;
  const relative = pieces.filter(Boolean).join('/');
  if (!exact.has(relative) && !directories.some(prefix => relative.startsWith(prefix))) return null;
  const filename = resolve(root, relative);
  return filename.startsWith(root + sep) ? filename : null;
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
  const filename = filenameFor(req.url ?? '');
  if (!filename) { res.writeHead(404).end('Not found'); return; }
  let info;
  try { info = await stat(filename); }
  catch { res.writeHead(404).end('Not found'); return; }
  if (!info.isFile()) { res.writeHead(404).end('Not found'); return; }
  const extension = filename.slice(filename.lastIndexOf('.'));
  const type = types[extension];
  if (!type) { res.writeHead(404).end('Not found'); return; }
  res.writeHead(200, { 'Content-Type': type, 'Content-Length': info.size });
  if (req.method === 'HEAD') { res.end(); return; }
  const stream = createReadStream(filename);
  stream.on('error', () => res.destroy());
  stream.pipe(res);
});
server.listen(port, host, () => {
  console.log(`Funes Vision synthetic preview: http://127.0.0.1:${port}/`);
});
