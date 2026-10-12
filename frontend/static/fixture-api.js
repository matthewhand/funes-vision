/* Funes Vision synthetic preview bridge.
 * This page intentionally has NO camera connection, model inference, write API,
 * or external network integration. Only recorded fictional fixture records.
 */
(() => {
  'use strict';
  window.__FUNES_FIXTURE_ONLY__ = true;
  const nativeFetch = window.fetch.bind(window);
  const fixtureURL = new URL('./fixture-data.json', document.currentScript.src);
  let fixturePromise;
  const jsonResponse = (body, status = 200) => new Response(JSON.stringify(body), {
    status, headers: { 'content-type': 'application/json; charset=utf-8',
                       'cache-control': 'no-store' }
  });
  function fixtures() {
    if (!fixturePromise) {
      fixturePromise = nativeFetch(fixtureURL)
        .then(response => { if (!response.ok) throw Error('Synthetic fixture payload unavailable'); return response.json(); });
    }
    return fixturePromise;
  }
  window.fetch = async function syntheticReadOnlyFetch(input, options = {}) {
    const raw = input instanceof Request ? input.url : String(input);
    const requested = new URL(raw, location.href);
    const method = String(options.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    if (requested.origin !== location.origin) {
      return jsonResponse({ error: 'Outside network disabled in synthetic preview' }, 403);
    }
    // Fail closed for *all* mutations, not merely known API routes. Never
    // manufacture a successful pin, deletion, settings change, or inference.
    if (method !== 'GET') return jsonResponse({ error: 'Demo is read-only; no changes saved' }, 403);
    const path = requested.pathname;
    if (!path.startsWith('/api/')) {
      if (path.startsWith('/frontend/') && !path.includes('..')) return nativeFetch(input, options);
      return jsonResponse({ error: 'Unlisted resource' }, 404);
    }
    const data = await fixtures();
    switch (path) {
      case '/api/cameras': return jsonResponse(data.cameras);
      case '/api/settings': return jsonResponse(data.settings);
      case '/api/status': return jsonResponse(data.status);
      case '/api/taxonomy': return jsonResponse(data.taxonomy);
      case '/api/integrations': return jsonResponse(data.integrations);
      case '/api/inference_log': return jsonResponse(data.inference_log);
      case '/api/llm-schema': return jsonResponse(data.llm_schema);
      case '/api/pins': return jsonResponse([]);
      case '/api/catalogs': {
        const camera = requested.searchParams.get('camera');
        if (!camera || camera === 'all') return jsonResponse(data.catalogs);
        return data.catalogs[camera] ? jsonResponse(data.catalogs[camera]) : jsonResponse({ error: 'Unknown synthetic camera' }, 404);
      }
      case '/api/health': return jsonResponse({ status: 'degraded', synthetic_fixture: true,
        checks: { inotify: false, llm_reachable: true, recent_sweep: false,
                  disk_space: true, deep_passes_disabled: true }, cameras: data.cameras });
      default: return jsonResponse({ error: 'API endpoint unavailable in synthetic preview' }, 404);
    }
  };
  // No native SSE (a browser would retry an unavailable endpoint endlessly).
  // The page stays frozen until a user explicitly requests a fixture reload.
  if ('EventSource' in window) {
    window.EventSource = class DisabledSyntheticEventSource {
      constructor() { throw Error('No live event stream in the synthetic preview'); }
    };
  }
})();
