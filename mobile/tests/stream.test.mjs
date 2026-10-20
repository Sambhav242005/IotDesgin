import assert from 'node:assert/strict';
import test from 'node:test';
import { processCameraFrame } from '../src/stream.ts';

test('stream preserves the exact camera frame before preview upload and cleans only its temporary file', async () => {
  const frame = { uri: 'camera-cache.jpg', width: 640, height: 480 };
  const events = [];
  await assert.rejects(processCameraFrame({
    capture: async () => frame,
    active: () => true,
    persist: async value => { assert.equal(value, frame); events.push('durable-copy'); },
    publish: async value => { assert.equal(value, frame); events.push('upload'); throw Error('offline'); },
    removeTemporary: async uri => { assert.equal(uri, frame.uri); events.push('delete-cache'); },
  }), /offline/);
  assert.deepEqual(events, ['durable-copy', 'upload', 'delete-cache']);
});

test('pausing while a camera frame is acquired prevents late persistence and upload', async () => {
  const events = [];
  await processCameraFrame({
    capture: async () => ({ uri: 'late.jpg', width: 640, height: 480 }),
    active: () => false,
    persist: async () => { events.push('save'); },
    publish: async () => { events.push('upload'); },
    removeTemporary: async () => { events.push('delete-cache'); },
  });
  assert.deepEqual(events, ['delete-cache']);
});

test('finishing during persistence keeps the saved input and suppresses a late preview request', async () => {
  let active = true;
  const events = [];
  await processCameraFrame({
    capture: async () => ({ uri: 'last.jpg', width: 640, height: 480 }),
    active: () => active,
    persist: async () => { events.push('saved'); active = false; },
    publish: async () => { events.push('upload'); },
    removeTemporary: async () => { events.push('delete-cache'); },
  });
  assert.deepEqual(events, ['saved', 'delete-cache']);
});
