/* Read-only adapter for the Python watched-folder activity v1 JSON contract.
   Never mixes the prerecorded Refined Current fixtures with folder observations. */
(function (root) {
  'use strict';
  const MAX_FRAMES = 500;
  const CAMERAS = ['DemoFront', 'DemoBack'];
  const IMAGE_NAME = /^[A-Za-z0-9_-][A-Za-z0-9_.-]*\.jpe?g$/i;
  const UTC_TIME = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/;
  const allowedKeys = new Set(CAMERAS);

  function fail(message) { throw new Error('Invalid activity-v1 response: ' + message); }
  function plainObject(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
  }
  function time(value) {
    if (typeof value !== 'string' || !UTC_TIME.test(value) || !Number.isFinite(Date.parse(value))) fail('timestamp');
    return value;
  }
  function strings(value, limit) {
    if (!Array.isArray(value) || value.length > limit || value.some(x => typeof x !== 'string' || x.length > 100)) fail('annotation list');
    return [...value];
  }
  function normalizeActivity(payload) {
    if (!plainObject(payload) || payload.schema_version !== 1 ||
        payload.data_source !== 'watched-folder-preview' ||
        payload.read_only !== true || payload.mode !== 'watched-folder-demo') fail('source or schema');
    if (!plainObject(payload.inference) || payload.inference.object_detector !== 'not run' ||
        payload.inference.vision_decision_model !== 'not run') fail('inference provenance');
    if (!plainObject(payload.scan) || !Number.isInteger(payload.scan.max_frames) ||
        payload.scan.max_frames < 1 || payload.scan.max_frames > MAX_FRAMES ||
        !Number.isInteger(payload.scan.max_candidates_per_camera) ||
        payload.scan.max_candidates_per_camera < 1 ||
        typeof payload.scan.possibly_truncated !== 'boolean') fail('scan bounds');
    if (!Array.isArray(payload.frames) || payload.frames.length > MAX_FRAMES ||
        !Array.isArray(payload.visits) || payload.visits.length > MAX_FRAMES ||
        payload.count !== payload.frames.length || payload.visit_count !== payload.visits.length) fail('counts');
    const ids = new Map();
    const frames = payload.frames.map(frame => {
      if (!plainObject(frame) || typeof frame.id !== 'string' ||
          !/^[a-f0-9]{16}$/.test(frame.id) || ids.has(frame.id) ||
          !allowedKeys.has(frame.camera) || typeof frame.filename !== 'string' ||
          !IMAGE_NAME.test(frame.filename) || frame.filename.includes('..') ||
          frame.image_url !== '/captures/' + frame.camera + '/' + frame.filename ||
          typeof frame.description !== 'string' || frame.description.length > 300 ||
          typeof frame.synthetic_fixture !== 'boolean' ||
          frame.detector_status !== 'not run' || frame.decision_model_status !== 'not run') fail('frame identity or provenance');
      const capturedAt = time(frame.captured_at);
      const expectedSource = frame.synthetic_fixture ? 'synthetic-fixture' : 'filesystem-mtime';
      const expectedAnnotation = frame.synthetic_fixture ? 'synthetic fixture annotation' : 'unclassified image';
      if (frame.capture_time_source !== expectedSource || frame.annotation_source !== expectedAnnotation) fail('frame origin');
      const objects = strings(frame.objects, 32);
      if (!frame.synthetic_fixture && objects.length) fail('unclassified objects');
      const normalized = {
        id: frame.id, camera: frame.camera, filename: frame.filename,
        image_url: frame.image_url, captured_at: capturedAt,
        capture_time_source: expectedSource, description: frame.description,
        synthetic_fixture: frame.synthetic_fixture, objects,
        annotation_source: expectedAnnotation
      };
      ids.set(frame.id, normalized);
      return normalized;
    });
    const assigned = new Set(), visitIds = new Set();
    const visits = payload.visits.map(visit => {
      if (!plainObject(visit) || !allowedKeys.has(visit.camera) ||
          !Array.isArray(visit.frames) || !visit.frames.length ||
          visit.frames.length > MAX_FRAMES || visit.frame_count !== visit.frames.length ||
          typeof visit.id !== 'string' || !/^visit-[1-9]\d*$/.test(visit.id)) fail('visit shape');
      if (visitIds.has(visit.id)) fail('duplicate visit ID');
      visitIds.add(visit.id);
      const start = time(visit.start), end = time(visit.end);
      if (Date.parse(start) > Date.parse(end)) fail('visit time order');
      const objects = strings(visit.objects, 32);
      for (const id of visit.frames) {
        const source = ids.get(id);
        if (!source || source.camera !== visit.camera || assigned.has(id) ||
            Date.parse(source.captured_at) < Date.parse(start) ||
            Date.parse(source.captured_at) > Date.parse(end)) fail('visit/frame relationship');
        assigned.add(id);
      }
      const derived = new Set(visit.frames.flatMap(id => ids.get(id).objects));
      if (objects.length !== derived.size || objects.some(x => !derived.has(x))) fail('visit annotation provenance');
      return { id: visit.id, camera: visit.camera, start, end,
        frame_ids: [...visit.frames], objects, frame_count: visit.frame_count };
    });
    if (assigned.size !== frames.length) fail('unassigned frame');
    return {frames, visits, count:frames.length, visit_count:visits.length,
      possibly_truncated:payload.scan.possibly_truncated, max_frames:payload.scan.max_frames};
  }

  function mount({document, fetchActivity, timeoutMs=5000}) {
    if (!document || typeof fetchActivity !== 'function') throw new Error('Missing local activity source');
    let selected = 'all', current = null, busy = false, lastGood = null;
    const el = id => document.getElementById(id);
    const labels = {DemoFront:'Front entrance', DemoBack:'Back garden'};
    const make = (tag, className, text) => {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = String(text);
      return node;
    };
    const format = date => new Date(date).toLocaleString();
    function paint() {
      if (!current) return;
      const frames = current.frames.filter(f => selected === 'all' || f.camera === selected);
      const visits = current.visits.filter(v => selected === 'all' || v.camera === selected);
      el('frame-count').textContent = String(frames.length);
      el('visit-count').textContent = String(visits.length);
      const gallery = el('activity-frames'), episodes = el('activity-visits');
      const frameNodes = frames.map(frame => {
        const card = make('article','activity-card');
        const img = make('img','activity-photo');
        img.src = frame.image_url; img.loading = 'lazy';
        img.alt = frame.synthetic_fixture ? 'Fictional fixture: ' + frame.description : 'Unclassified local JPEG';
        img.referrerPolicy = 'no-referrer';
        const body = make('div','activity-card-body');
        body.append(make('h3','',frame.description),
          make('p','activity-meta',labels[frame.camera] + ' · ' + format(frame.captured_at)),
          make('p','activity-provenance',frame.synthetic_fixture ?
            'Synthetic fixture annotation · Models not run' :
            'Unclassified JPEG · Filesystem timestamp · Models not run'));
        if (frame.objects.length) body.append(make('p','activity-meta','Fixture objects: ' + frame.objects.join(', ')));
        card.append(img,body);
        return card;
      });
      gallery.replaceChildren(...(frameNodes.length ? frameNodes : [make('p','empty-state','No JPEGs in this view.')]));
      const visitNodes = visits.map(visit => {
        const item = make('article','visit-card');
        item.append(make('h3','',labels[visit.camera] + ' · ' + visit.frame_count + ' frame' + (visit.frame_count===1?'':'s')),
          make('p','activity-meta',format(visit.start) + ' – ' + format(visit.end)));
        if (visit.objects.length) item.append(make('p','activity-provenance','Fictional fixture objects: ' + visit.objects.join(', ')));
        return item;
      });
      episodes.replaceChildren(...(visitNodes.length ? visitNodes : [make('p','empty-state','No visits in this view.')]));
      el('truncation').hidden = !current.possibly_truncated;
    }
    function status(state, detail) {
      const node = el('source-status'), warning=el('stale-notice');
      node.textContent = detail;
      node.dataset.state = state;
      warning.hidden = state !== 'stale';
    }
    async function refresh() {
      if (busy) return;
      busy = true;
      el('refresh').disabled = true;
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const response = await fetchActivity('/api/activity', {
          method:'GET', cache:'no-store', redirect:'error', credentials:'same-origin', signal:controller.signal
        });
        if (!response.ok) throw new Error('HTTP status ' + response.status);
        const normalized = normalizeActivity(await response.json());
        current = normalized; lastGood = Date.now();
        paint();
        status('ready', 'Local activity-v1 · updated ' + new Date(lastGood).toLocaleTimeString() + ' · models not run');
      } catch (_) {
        status(lastGood===null?'unavailable':'stale',
          lastGood===null?'Activity API unavailable or incompatible — no data loaded':
          'Activity API unavailable or incompatible — last good snapshot ' + new Date(lastGood).toLocaleTimeString());
      } finally {
        clearTimeout(timer);busy = false;el('refresh').disabled = false;
      }
    }
    for (const button of document.querySelectorAll('[data-camera]')) {
      button.addEventListener('click',() => {
        selected = button.dataset.camera;
        for (const choice of document.querySelectorAll('[data-camera]'))
          choice.setAttribute('aria-pressed',String(choice===button));
        paint();
      });
    }
    el('refresh').addEventListener('click', refresh);
    refresh();
    return {refresh};
  }

  const exported = {normalizeActivity, mount};
  if (typeof module !== 'undefined' && module.exports) module.exports = exported;
  if (root && root.document) {
    root.FunesActivityV1 = exported;
    root.addEventListener('DOMContentLoaded', () => {
      mount({document:root.document, fetchActivity:root.fetch.bind(root)});
    });
  }
})(typeof window === 'undefined' ? null : window);
