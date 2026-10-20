import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import test from 'node:test';
import vm from 'node:vm';

// Run the shipped scripts with a small DOM and controlled API responses. These
// exercise coordination and race handling; WebGL/HLS decoding need a browser.
class Element {
  children = []; attributes = new Map(); dataset = {}; style = {}; value = '';
  textContent = ''; checked = false; hidden = false; disabled = false;
  readyState = 0; currentTime = 0; paused = true; seeking = false;
  duration = 30; seekable = { length: 0 };
  listeners = new Map(); contentWindow = { messages: [], postMessage(message) { this.messages.push(message); } };
  get options() { return this.children; }
  replaceChildren(...children) { this.children = children; }
  append(...children) { this.children.push(...children); }
  setAttribute(name, value) { this.attributes.set(name, value); }
  getAttribute(name) { return this.attributes.get(name); }
  addEventListener(name, listener) { this.listeners.set(name, listener); }
  removeAttribute() {}
  pause() { this.paused = true; }
  play() { this.paused = false; return Promise.resolve(); }
  canPlayType() { return ''; }
  load() {}
}
const origin = 'https://localhost:8443';
const session = 'a'.repeat(32), sessionB = 'b'.repeat(32);
function environment() {
  const elements = new Map(), listeners = new Map(), messages = [];
  const document = { hidden: false, readyState: 'loading', documentElement: { style: { contentVisibility: 'auto' } },
    getElementById(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
    createElement() { return new Element(); } };
  const context = vm.createContext({ document, location: { origin, pathname: '/desktop/scans/example/view' },
    AbortSignal, URL, Map, console, setTimeout() { return 1; }, clearTimeout() {},
    ResizeObserver: class { observe() {} }, fetch: () => new Promise(() => {}),
    addEventListener(name, listener) { listeners.set(name, listener); },
    parent: { postMessage(message) { messages.push(message); } }, window: {} });
  return { context, elements, listeners, messages, document };
}
async function sceneHarness() {
  const env = environment();
  const html = readFileSync(new URL('../server/iotdesign/scene.html', import.meta.url), 'utf8');
  const script = html.match(/<script type="module">([\s\S]*?)<\/script>/)[1]
    .replace("const THREE=await import('three');", "throw Error('WebGL unavailable in DOM harness');const THREE=null;");
  env.api = await vm.runInContext(`(async()=>{${script}\nreturn {
    setTimeline(data){timeline=data;frameById=new Map(data.frames.map(frame=>[frame.id,frame]));rebuildSourceSessions()},
    setRenderer(fn){renderCloud=fn},setDisplayed(id){lastJob=id;chosenJob=id},
    state(){return {lastJob,chosenJob,selectedFrameId,sourceSession,loadingJob,followSnapshots}},
    frameAtTime,selectSourceFrame,loadCompletedSnapshot,chooseSnapshot,drawFilmstrip,renderComparison
  }})()`, env.context);
  return env;
}
const frame = (ordinal, sess = session) => ({ id: `video-${sess}-${ordinal}`, video_session_id: sess,
  video_frame: ordinal, video_pts_seconds: (ordinal - 1) / 30, video_time_source: 'nominal_remux_30fps',
  width: 1280, height: 720, status: 'completed', job_ids: [] });
const snapshot = (id, frames) => ({ job_id: id, frame_ids: frames.map(item => item.id), point_count: 123,
  tracking_status: 'image_only' });
function response(data) { return { ok: true, json: async () => data, arrayBuffer: async () => new Float32Array([0, 0, 0, 1, 1, 1]).buffer }; }
function sceneResponses(url) {
  const id = new URL(url, origin).searchParams.get('job_id');
  return response(url.includes('/scene/points') ? null : url.includes('/scene/cameras') ? [] : { completed_job_id: id, metrics: {} });
}

test('both shipped desktop scripts parse as JavaScript', () => {
  const directory = mkdtempSync(join(tmpdir(), 'iotdesign-replay-'));
  for (const name of ['scene', 'player']) {
    const html = readFileSync(new URL(`../server/iotdesign/${name}.html`, import.meta.url), 'utf8');
    const script = name === 'scene' ? html.match(/<script type="module">([\s\S]*?)<\/script>/)[1] : html.match(/<script>([\s\S]*?)<\/script>/)[1];
    const path = join(directory, `${name}.mjs`); writeFileSync(path, script); execFileSync(process.execPath, ['--check', path]);
  }
});

test('sparse historical source frames never substitute for missing playback ordinals', async () => {
  const { api } = await sceneHarness(); const frames = [frame(1), frame(60), frame(120)];
  api.setTimeline({ frames, snapshots: [] });
  assert.equal(api.frameAtTime(session, 59 / 30).id, frames[1].id);
  assert.equal(api.frameAtTime(session, 30 / 30), null);
  assert.equal(api.frameAtTime(sessionB, 59 / 30), null);
});

test('selecting a source frame binds its historic completed batch and job download', async () => {
  const env = await sceneHarness(); const frames = [frame(1), frame(2)];
  env.api.setTimeline({ frames, snapshots: [snapshot('old', [frames[0]]), snapshot('new', [frames[1]])] });
  env.api.setRenderer(() => {}); env.api.setDisplayed('new'); env.context.fetch = async url => sceneResponses(url);
  await env.api.selectSourceFrame(frames[0].id, { seek: true });
  assert.equal(env.api.state().lastJob, 'old'); assert.equal(env.api.state().followSnapshots, false);
  assert.match(env.document.getElementById('download').href, /job_id=old$/);
  const message = env.document.getElementById('player').contentWindow.messages.at(-1);
  assert.equal(message.sessionId, session); assert.equal(message.seconds, 0);
});

test('a late historic response cannot replace a subsequently selected batch', async () => {
  const env = await sceneHarness(); env.api.setRenderer(() => {});
  const frames = [frame(1), frame(2)];env.api.setTimeline({ frames, snapshots: [snapshot('old', frames), snapshot('new', frames)] });
  const waiting = [];
  env.context.fetch = url => new URL(url, origin).searchParams.get('job_id') === 'old'
    ? new Promise(resolve => waiting.push(() => resolve(sceneResponses(url)))) : Promise.resolve(sceneResponses(url));
  const earlier = env.api.loadCompletedSnapshot('old');
  await env.api.loadCompletedSnapshot('new'); waiting.forEach(resolve => resolve()); await earlier;
  assert.equal(env.api.state().lastJob, 'new'); assert.equal(env.api.state().chosenJob, 'new');
});

test('missing playback source clears camera selection instead of showing a stale match', async () => {
  const env = await sceneHarness(); const frames = [frame(1), frame(60)];env.api.setTimeline({ frames, snapshots: [] });
  await env.api.selectSourceFrame(frames[0].id, { loadSnapshot: false });
  await env.listeners.get('message')({ origin, source: env.document.getElementById('player').contentWindow,
    data: { type: 'iotdesign-video-time', sessionId: session, seconds: 1, following: false } });
  assert.equal(env.api.state().selectedFrameId, null);
  assert.equal(env.document.getElementById('selected-source').hidden, true);
  assert.match(env.document.getElementById('frame-detail').textContent, /No preserved source frame matches playback frame 31/);
});

test('a failed 3D initialization remains visible when a completed batch is selected', async () => {
  const env = await sceneHarness(); const before = env.document.getElementById('error').textContent;
  await env.api.loadCompletedSnapshot('old');
  assert.equal(env.document.getElementById('error').textContent, before);
  assert.match(before, /3D viewer unavailable/);assert.equal(env.api.state().lastJob, null);
  assert.match(env.document.getElementById('revision').textContent, /3D rendering is unavailable/);
});

test('phone diagnostics expose insufficient timing evidence independently of COLMAP quality', async () => {
  const env = await sceneHarness();env.api.renderComparison({ selected_frames: 43, reconstruction: { points: 674,
    quality: { status: 'sufficient', mean_reprojection_error_px: .824 } }, comparison: { status: 'inconclusive',
    reason: 'insufficient_registered_coverage', registered_frames: 27, matched_cameras: 0,
    frame_status_counts: { timestamp_mismatch: 22, tracking_not_active: 5 } } });
  assert.match(env.document.getElementById('comparison-status').textContent, /inconclusive/);
  const text = env.document.getElementById('comparison-summary').children.map(item => item.textContent).join(' ');
  assert.match(text, /27 \/ 43/);assert.match(text, /timestamp mismatch: 22/);assert.match(text, /heuristic checks do not prove/);
});

test('cross-session video seek pauses on the requested media time and suppresses stale time messages', async () => {
  const env = environment(), html = readFileSync(new URL('../server/iotdesign/player.html', import.meta.url), 'utf8');
  const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  vm.runInContext(`${script}\nglobalThis.api={load,announceTime,state:()=>({selected,pendingSeek,pausedForSelection})}`, env.context);
  env.api = env.context.api;const video = env.document.getElementById('video');
  env.api.load({ id: session, complete: true }); video.readyState = 1;video.currentTime = 20;video.listeners.get('loadedmetadata')();
  env.listeners.get('message')({ origin, source: env.context.parent, data: { type: 'iotdesign-seek-video', sessionId: sessionB, seconds: 2 } });
  env.api.load({ id: sessionB, complete: true });env.api.announceTime(20);assert.equal(env.messages.length, 0);
  video.listeners.get('loadedmetadata')();assert.equal(video.currentTime, 2);assert.equal(video.paused, true);
  env.api.announceTime(20);assert.equal(env.messages.length, 0);
  video.listeners.get('seeked')();assert.equal(env.messages.at(-1).seconds, 2);assert.equal(env.messages.at(-1).sessionId, sessionB);
  env.listeners.get('message')({ origin, source: env.context.parent, data: { type: 'iotdesign-seek-video', sessionId: sessionB, seconds: 100 } });
  assert.equal(video.currentTime, 2);assert.equal(env.api.state().pendingSeek.seconds, 100);
  video.listeners.get('seeked')();assert.equal(video.currentTime, 2);
  assert.match(env.document.getElementById('status').textContent, /not in the playable video segments yet/);
});
