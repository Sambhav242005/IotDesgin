import assert from 'node:assert/strict';
import test from 'node:test';
import { isSupportedApiVersion, normalizeAddress, parsePairing, prioritizeDiscoveredServer } from '../src/protocol.ts';

const server = (serverId, host) => ({
  serverId, name: `PC ${serverId}`, apiVersion: '1', host, port: 8443, fingerprint: 'a'.repeat(64),
});

test('manual server addresses normalize to HTTPS and the app port', () => {
  assert.equal(normalizeAddress('192.168.1.25'), 'https://192.168.1.25:8443');
  assert.equal(normalizeAddress('https://room-pc.local:9000'), 'https://room-pc.local:9000');
  assert.throws(() => normalizeAddress('http://192.168.1.25'), /Enter the PC address/);
  assert.throws(() => normalizeAddress('https://user@192.168.1.25'), /Enter the PC address/);
});

test('pairing QR payload is validated before connection', () => {
  const payload = { type: 'iotdesign-pair', version: 1, address: 'https://192.168.1.25:8443',
    server_id: 'pc-1', fingerprint: 'a'.repeat(64), code: '00420017' };
  assert.equal(parsePairing(JSON.stringify(payload)).code, '00420017');
  assert.throws(() => parsePairing(JSON.stringify({ ...payload, fingerprint: 'bad' })));
  assert.throws(() => parsePairing(JSON.stringify({ ...payload, code: '123' })));
});

test('discovery reconnects to the saved PC, pairs one new PC, and asks when several appear', () => {
  const first = server('pc-1', '192.168.1.20');
  const second = server('pc-2', '192.168.1.21');
  assert.deepEqual(prioritizeDiscoveredServer(null, []), { type: 'none' });
  assert.equal(prioritizeDiscoveredServer(null, [first]).type, 'pair');
  assert.deepEqual(prioritizeDiscoveredServer('pc-2', [first, second]), { type: 'reconnect', server: second });
  assert.deepEqual(prioritizeDiscoveredServer(null, [first, second]), { type: 'choose', servers: [first, second] });
});

test('only API v1 can be paired with this mobile client', () => {
  assert.equal(isSupportedApiVersion(1), true);
  assert.equal(isSupportedApiVersion(2), false);
  assert.equal(isSupportedApiVersion(1.5), false);
});
