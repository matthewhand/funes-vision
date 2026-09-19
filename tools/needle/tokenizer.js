// tools/needle/tokenizer.js — SentencePiece wrapper for the Needle model.
//
// Loaded lazily by tools/needle-client.js. The tokenizer is tiny (125 KB) and
// is the one piece of the stack that is not WASM, so it loads first and fast.

export async function loadTokenizer(path) {
  // sentencepiece-js is fetched on demand, like the ONNX runtime.
  const { SentencePieceProcessor } = await import(
    'https://cdn.jsdelivr.net/npm/sentencepiece-js@0.2.1/dist/sentencepiece.min.js'
  );
  const sp = new SentencePieceProcessor();
  await sp.load(path);
  return { encode: (s) => sp.encode(s, { bos: true, eos: false }) };
}