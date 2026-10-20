import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// Execute the shipped dashboard script against a minimal DOM. No browser,
// device, server credentials or camera access is needed for selection tests.
function dashboard() {
  class Element {
    children = [];
    _value = '';
    textContent = '';
    get value() { return this._value || this.children[0]?.value || ''; }
    set value(value) { this._value = value; }
    replaceChildren(...children) { this.children = children; this._value = ''; }
    addEventListener() {}
    removeAttribute() {}
  }
  const elements = new Map();
  const document = {
    hidden: false,
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, new Element());
      return elements.get(id);
    },
    createElement() { return new Element(); },
  };
  const html = readFileSync(new URL('../../server/iotdesign/desktop.html', import.meta.url), 'utf8');
  const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  const context = vm.createContext({ document, setTimeout() {}, setInterval() {},
    fetch: () => new Promise(() => {}), AbortSignal, URL });
  vm.runInContext(script, context);
  return { context, select: document.getElementById('live-scan') };
}

test('dashboard follows a live scan rather than an empty newer scan', () => {
  const { context, select } = dashboard();
  context.scans = [{ id: 'new-empty', name: 'Room', state: 'capturing' },
    { id: 'phone-streaming', name: 'Room', state: 'capturing' }];
  vm.runInContext('updateLiveScans(scans, [])', context);
  assert.equal(select.value, 'new-empty');
  vm.runInContext("updateLiveScans(scans, ['phone-streaming'])", context);
  assert.equal(select.value, 'phone-streaming');
});

test('dashboard preserves an already live selection and excludes completed scans', () => {
  const { context, select } = dashboard();
  context.scans = [{ id: 'done', name: 'Room', state: 'completed' },
    { id: 'first', name: 'Room', state: 'capturing' },
    { id: 'second', name: 'Room', state: 'capturing' }];
  vm.runInContext("updateLiveScans(scans, ['first', 'second'])", context);
  select.value = 'second';
  vm.runInContext("updateLiveScans(scans, ['first', 'second'])", context);
  assert.equal(select.value, 'second');
  assert.equal(select.children.length, 2);
});
