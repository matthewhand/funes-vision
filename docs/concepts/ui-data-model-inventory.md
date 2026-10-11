# Funes Vision – UI data-model inventory (for the redesign concepts)

Status: draft, evidence-based. Every field below was read from the code or from the synthetic fixtures
(`tools/screenshots/fixtures/`). Where the data does **not** contain something (notably any detector or LLM
confidence score) this document says so; the concepts must not invent it.

Sources: `catalog.py` (row contract, schema 1), `taxonomy.py`, `analyze_images.py` (`FRONT_SCHEMA`, `BACK_SCHEMA`,
`merge_llm_into_fastpass`), `pipeline_events.py`, `api_server.py` (`/api/*`), `index.html`
(`computeLabelStates`, `computeVisits`, `renderEventsView`), fixtures `gallery/{analysis,bursts,images}.json`,
`api/{status,inference_log,settings}.json`.

## 1. Entities

| Entity | Identity | Where it lives | Notes |
|---|---|---|---|
| Frame (snapshot) | filename `IP_CHANNEL_YYYYMMDDHHMMSSmmm_MOTDEC.jpg` (e.g. `10.0.0.21_01_20260618101522301_MOTDEC.jpg`) | `images.json` (array of filenames, newest first), thumbs at `thumbs/<name>` | Camera = IP + channel; time is encoded in the name and rendered in the display timezone (`status.timezone`, e.g. Australia/Sydney). "MOTDEC" = motion-detected capture. No width/height/size field is exposed per frame. |
| Analysis row | filename | `analysis.json` `{filename: row}` | Flat dict; contract in `catalog.py`. Absent row = never analysed (UI shows scanned/clear text). |
| Burst | key = last frame's filename | `bursts.json` `{key: {summary, images[]}}` | `summary` is an LLM-written caption (e.g. "A courier walks a parcel up the front driveway to the door."); `images` are the frames of the burst, oldest first. Only some frames have a burst (2 of 10 in the fixture). |
| Visit (Timeline row) | derived client-side by `computeVisits(filenames)` | not stored | `{label, start, end, count, ongoing, caption, frames[]}`; `label` = the canonical object label; `ongoing` when the last frame is recent; `caption` from the burst summary / `_llm` description; player/clip capped at 60 frames; list capped at 300 visits. |
| Object label | string | keys of the analysis row that are `true` (detector: `person, dog, car, cat, bird, face, body`) + `_yolo` array | Aliases via `state.labelAliases`; HA/scene flags are excluded from object identity (see 3). |
| Scene flag (HA flag) | key in `taxonomy.HA_FLAG_KEYS` | LLM output merged into the row | Booleans and short strings, e.g. `postal_delivery`, `dog_walked`, `porch_access`, `animal_detected`, `animal_type`; rendered as badges, never as object chips. |
| Camera | name (`front`, `back`) | `/api/cameras`, `status.cameras[]` | `{name, images, bytes, budget_pct, last_frame_age_s, stale}`. Front uses `FRONT_SCHEMA`, back uses `BACK_SCHEMA` (different flag sets). |
| Inference log entry | none (list) | `/api/inference_log` (newest 50) | `{image, model, trigger, started (unix s), duration_s, labels[], ok}`; fixture trigger value: `priority`. |
| Pipeline event | `{ts, event, ...}` | SSE `/api/events` | Event names emitted by the analyzer: `image.new`, `detection.preliminary`, `new-detection`, `new-burst`. |

## 2. Analysis row fields (observed in fixtures, 10 rows)

| Field | Type / values | Meaning | Present when |
|---|---|---|---|
| `fast_pass` | `"negative"` \| `"partial"` | detector stage result; `negative` = nothing found; `partial` = detector hit awaiting / lacking an LLM verdict | not present on verified rows or bare detector rows |
| `_yolo` | array of label strings | detector labels that were true (stamped by `catalog.stamp`, which also sets `_schema: 1`) | all analysed rows with a hit |
| `person, dog, car, cat, bird, face, body` | `true` | individual detector presence keys (`YOLO_PRESENCE_KEYS`) | only when true |
| `_llm` | object (non-empty) | the LLM's structured answer, merged flat into the row too | after a successful LLM pass |
| `_llm_model`, `_llm_ms` | string, integer ms | model name (fixture: `gemma4:e2b`) and duration | with `_llm` or a skip |
| `description` | string | LLM description / caption | with `_llm` |
| `_llm_skip` | string, e.g. `no_trigger`, `no_scan`, `llm_failed` (other reasons possible via `skip_reason`) | why no LLM verdict exists | LLM not run / failed |
| `_llm_raw`, `_centres`, `_timeline_images` | raw text, per-label centre map, list | diagnostics / geometry / burst frames; **not** rendered today | optional |
| scene flags | see taxonomy | e.g. `postal_delivery`, `postal_how`, `dog_walked`, `car_access`, `car_color`, `car_make`, `animal_detected`, `animal_type`, `approaching_house`, `weapon_detected`, `clothes_drying` | only on LLM-verified rows |

**There is no confidence/score field anywhere** in `analysis.json`, `status.json`, `inference_log.json` or the SSE
events. Detector output is boolean per label; LLM output is booleans/enums/short strings. A concept may show
"detected by YOLO", "confirmed by Gemma", model name and duration, but must never display a percentage or
probability bar.

## 3. Pipeline stages (in order) and the states they produce

1. **Motion trigger** – the camera writes a `…_MOTDEC.jpg` frame. State: *pending* (row absent, `status.queue.unanalyzed` counts these).
2. **Fast pass (detector, YOLO by default; `settings.fast_pass_engine`)** – sets boolean labels or `fast_pass: "negative"`. 
3. **System 1 / LLM pass (Gemma, `gemma4:e2b`)** – runs on `person/dog/cat/bird/face/body` hits except `GATE_IGNORE_LABELS` (default `car`); front vs back schema; result merged into the row; (see events below). If skipped/failed: `_llm_skip` set and `fast_pass:"partial"` (skip with reason).
4. **Burst summary** – optional (`burst_summaries_enabled`): caption per burst; emits `new-burst`.
   **Events** (emitted when a row is written, `analyze_images.py` ~L1440): `image.new {file}` when the row did not exist; `new-detection {file, labels}` when the row is `verified` and has detector labels; `detection.preliminary {file, labels}` when the row is `preliminary` or `no_trigger` and has detector labels; `new-burst` for burst summaries. Every event also carries `ts` and `event`.
5. **Persistence** – `catalog.is_timeline_persistable`: only LLM-verified visits with an activity flag or non-ignored label outlive `max_age_days` (capped by `persist_budget_pct`).

Authoritative row classification (`catalog.kind`, precedence order): `verified` → `no_trigger` → `negative` → `skip` → `preliminary` → `empty`.

| State (UI vocabulary) | How it is derived | What the UI can honestly say |
|---|---|---|
| **verified** | `_llm` non-empty dict and no `_llm_skip` | "Confirmed by <model>" + description + flags |
| **preliminary** | detector label true, no verdict (`fast_pass:"partial"` or bare detector row) | "<label>? – awaiting confirmation" (the SPA already renders amber `label?`). If deep passes are OFF (`settings.deep_passes_enabled=false`) the detector **is** the final answer and `badgeStateFor` shows it as verified. |
| **negative** | `fast_pass == "negative"` | "No candidates" |
| **no_trigger** | `_llm_skip == "no_trigger"` (e.g. car-only) | "Detected <label>; not sent for deep analysis" |
| **skip / failed** | other `_llm_skip` (`no_scan`, `llm_failed`…) | "Analysis skipped/failed: <reason>" |
| **pending** | no analysis row (or `status.queue.unanalyzed > 0`) | "Not analysed yet" |
| **unavailable** | system level, not per row: `status.llm.reachable == false` (and `/api/health` 503 `degraded`) | "Inference unavailable – showing detector results only"; rows keep their own state. Offline fixture added by #120. |
| **empty** | none of the above | "Scanned (clear)" |

## 4. System status fields (`/api/status`, `/api/health`)

- `timezone`, `watch_dirs`, `settings{fast_pass_engine, deep_backfill, deep_passes_enabled, burst_summaries_enabled, gate_ignore_labels, max_dir_gb, max_scans_per_image, model_primary}`.
- `trigger{inotify_active, idle_sweep_seconds, last_sweep_age_s}`.
- `llm{model, reachable, allow_cloud}`; `inference{}` (live inference status when running).
- `queue{images_on_disk, unanalyzed, unverified_partials, awaiting_backfill, llm_verified, deep_s_per_frame, deep_eta_s}`.
- `cameras[]{name, images, bytes, budget_pct, last_frame_age_s, stale}`; `filesystem{free_gb,…}`; `metrics{window_min, count, ok, failures, success_rate, local, cloud, avg_s, p95_s}`; `retention`.
- `/api/health` -> `{status: ok|degraded, checks{inotify, llm_reachable, recent_sweep, disk_space}, cameras[]}`; HTTP 503 when degraded.

## 5. What each concept can and cannot show

Can show (data exists): time, camera, object label(s), stage reached, verified/preliminary/negative/skipped state, model name, LLM duration, description/caption, scene flags, burst frames (filmstrip/scrub), visit duration + frame count + ongoing, queue backlog, stale camera, disk budget.

Cannot show (no data): confidence percentages, bounding boxes (only `_centres`, unrendered and not guaranteed), per-frame resolution/size, audio, person identity, zone names, motion-region masks, event thumbnails other than frame JPEGs.

Hard rule for fixtures: a synthetic fixture for a concept may only use the keys listed in section 2/4; any extra key must be flagged `// SYNTHETIC-ONLY` and the concept must degrade when it is absent.
