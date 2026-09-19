// tools/needle-client.js — in-browser natural-language filter builder.
//
// This module is the bridge between the gallery's search box and the Cactus
// Needle model. It is loaded lazily (dynamic import) so the page parses and
// renders before ~140 MB of WASM is requested, and the search box keeps using
// keyword matching until it is up — a slow link or a failed download never
// degrades the gallery.
//
// Interface:
//   await init()              — load weights, warm up the runtime. Throws on failure.
//   await translate(query, schema) — returns a filter object, or null on failure.
//
// The model is a tool caller: it is handed a *description* of the gallery's
// filter schema and returns a structured call. It never sees image data or
// analysis JSON, so nothing leaves the box for this feature regardless of how
// it is loaded. The schema is owned by index.html (filterToolSchema) and this
// module only knows how to run a model.
//
// The ONNX runtime is not bundled with this repo — it is fetched on demand
// from the CDN below, and only when the user actually types a query. If the
// fetch or the model fails, translate() returns null and the search box falls
// back to keyword matching silently.

const NEEDLE_ENCODER = '/tools/needle/encoder.onnx';
const NEEDLE_DECODER = '/tools/needle/decoder_step.onnx';
const NEEDLE_TOKENIZER = '/tools/needle/needle.model';

// Endpoint the model is trained to emit. The gallery's filters are already
// well-defined state, so the schema is small and stable.
const TOOL_NAME = 'set_filters';

let runtime = null;

export async function init() {
  if (runtime) return runtime;

  // Dynamic import of the ONNX runtime. Kept out of the initial bundle.
  const { InferenceSession } = await import('https://cdn.jsdelivr.net/npm/onnxruntime-web@1.21.0/dist/esm/onnxruntime-web.js');
  const { loadTokenizer } = await import('/tools/needle/tokenizer.js');

  const [encoder, decoder, tokenizer] = await Promise.all([
    InferenceSession.create(NEEDLE_ENCODER, { executionProviders: ['wasm'] }),
    InferenceSession.create(NEEDLE_DECODER, { executionProviders: ['wasm'] }),
    loadTokenizer(NEEDLE_TOKENIZER),
  ]);

  runtime = { session: { encoder, decoder }, tokenizer };
  return runtime;
}

// Run one turn: natural language -> structured filter call.
//
// Returns a plain object matching the schema's properties (view, objects,
// date, time_start, time_end, hour, search), or null if the model produced
// nothing usable. The caller validates every field against the real filter
// state before applying it, so a bad translation can only widen or narrow —
// never break — the view.
export async function translate(query, schema) {
  const r = runtime || (await init());
  if (!query || !query.trim()) return null;

  const prompt = buildPrompt(query, schema);
  const tokens = r.tokenizer.encode(prompt);

  // Encoder: single pass over the prompt.
  const encoderOut = await r.session.encoder.run({
    input_ids: tokens,
  });

  // Decoder: step until the tool-call terminator, with a KV cache so the
  // conversation stays bounded (Needle pins tools as KV sinks).
  const decoded = await decode(r, encoderOut);

  const call = parseToolCall(decoded);
  if (!call || call.name !== TOOL_NAME) return null;
  return call.arguments || null;
}

function buildPrompt(query, schema) {
  // Needle is a tool caller, not a chatbot: it retrieves-and-assembles. Give
  // it the tool description and the user sentence, nothing else.
  return [
    'Available tools:',
    JSON.stringify({ tools: [schema] }),
    '',
    'Query: ' + query.trim(),
    '',
    'Respond with only a tool call.',
  ].join('\n');
}

async function decode(r, encoderOut) {
  // Placeholder decoder loop. The real implementation runs decoder_step in a
  // JS loop, feeding present-KV back in and stopping on the tool-call token.
  // This stub exists so the module loads and fails cleanly; it is replaced by
  // the full KV-cache loop when the ONNX export is wired up.
  throw new Error('needle decoder not wired up yet');
}

function parseToolCall(decoded) {
  // The model emits JSON like {"name":"set_filters","arguments":{...}}. Be
  // lenient: strip markdown fences, tolerate whitespace, and return null for
  // anything that is not a parseable call.
  if (!decoded) return null;
  const s = String(decoded).trim();
  const fenced = s.match(/```(?:json)?\s*([\s\S]*?)```/);
  const json = fenced ? fenced[1].trim() : s;
  try {
    const parsed = JSON.parse(json);
    if (parsed && typeof parsed === 'object') return parsed;
  } catch (_) {}
  return null;
}