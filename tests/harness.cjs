'use strict';
/** Shared cross-platform test helpers: configurable Python, dynamic ports and
 *  readiness polling. No fixed sleeps, no hardcoded `python3`, no fixed ports. */
const { spawn, spawnSync } = require('node:child_process');
const { createServer } = require('node:net');
const { once } = require('node:events');

let cachedPython = null;
/**
 * Resolve a working Python interpreter. `FUNES_PYTHON` overrides everything
 * (space separated, e.g. `FUNES_PYTHON="py -3"`). Otherwise the platform's own
 * launcher names are probed, because `python3` does not exist on Windows and
 * `python` does not exist on most CI images.
 */
function pythonCommand() {
  if (cachedPython) return cachedPython;
  const explicit = process.env.FUNES_PYTHON;
  const candidates = explicit ? [explicit.trim().split(/\s+/)]
    : process.platform === 'win32' ? [['py', '-3'], ['python'], ['python3']]
      : [['python3'], ['python']];
  const tried = [];
  for (const [command, ...prefix] of candidates) {
    const probe = spawnSync(command, [...prefix, '-c', 'import sys;print(sys.version_info[0])'],
      { encoding: 'utf8', timeout: 20000 });
    tried.push(`${[command, ...prefix].join(' ')} (status ${probe.status})`);
    if (!probe.error && probe.status === 0) {
      cachedPython = { command, prefix };
      return cachedPython;
    }
  }
  throw new Error(`No usable Python interpreter. Tried: ${tried.join(', ')}. ` +
    'Set FUNES_PYTHON to override, for example FUNES_PYTHON="py -3".');
}

/** Spawn Python with UTF-8 forced for both streams regardless of OS locale. */
function spawnPython(args, options = {}) {
  const { command, prefix } = pythonCommand();
  return spawn(command, [...prefix, ...args], {
    encoding: 'utf8',
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1', PYTHONIOENCODING: 'utf-8',
      PYTHONUTF8: '1', ...(options.env || {}) },
    ...options,
  });
}

/** Run Python once and return the completed result. */
function runPython(args, options = {}) {
  const { command, prefix } = pythonCommand();
  return spawnSync(command, [...prefix, ...args], {
    encoding: 'utf8', timeout: 120000,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1', PYTHONIOENCODING: 'utf-8',
      PYTHONUTF8: '1', ...(options.env || {}) },
    ...options,
  });
}

/** Ask the OS for an unused loopback port instead of hardcoding one. */
async function freePort() {
  const listener = createServer();
  listener.listen(0, '127.0.0.1');
  await once(listener, 'listening');
  const { port } = listener.address();
  listener.close();
  await once(listener, 'close');
  return port;
}

/**
 * Poll a local URL until it answers or the deadline passes. This is a readiness
 * probe against a real socket, not a fixed sleep.
 */
async function waitForHttp(url, { timeoutMs = 20000, intervalMs = 50, child } = {}) {
  const deadline = Date.now() + timeoutMs;
  let lastError = 'no response';
  while (Date.now() < deadline) {
    if (child && child.exitCode !== null) throw new Error(`Server exited with ${child.exitCode}`);
    try {
      const response = await fetch(url, { cache: 'no-store' });
      if (response.ok || response.status < 500) return response;
      lastError = 'HTTP ' + response.status;
    } catch (error) { lastError = error.message; }
    await new Promise(resolve => setTimeout(resolve, intervalMs));
  }
  throw new Error(`Timed out waiting for ${url}: ${lastError}`);
}

/** Wait for a child process to exit, escalating to SIGKILL. */
async function stopProcess(child) {
  if (!child || child.exitCode !== null) return;
  const exited = once(child, 'exit');
  child.kill('SIGTERM');
  const timer = setTimeout(() => child.kill('SIGKILL'), 4000);
  try { await exited; } finally { clearTimeout(timer); }
}

module.exports = { pythonCommand, spawnPython, runPython, freePort, waitForHttp, stopProcess };