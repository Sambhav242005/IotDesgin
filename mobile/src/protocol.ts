import type { DiscoveredServer } from '../modules/iotdesign-network/src';

export type PairingPayload = {
  address: string;
  server_id: string;
  fingerprint: string;
  code: string;
};

export function normalizeAddress(value: string, defaultPort = 8443) {
  const raw = value.trim();
  const url = new URL(raw.includes('://') ? raw : `https://${raw}`);
  if (url.protocol !== 'https:' || !url.hostname || url.username || url.password || url.pathname !== '/') {
    throw new Error('Enter the PC address, such as 192.168.1.20:8443.');
  }
  if (!url.port) url.port = String(defaultPort);
  return url.origin;
}

export function parsePairing(data: string): PairingPayload {
  const payload = JSON.parse(data);
  if (payload.type !== 'iotdesign-pair' || payload.version !== 1 || typeof payload.address !== 'string' ||
      typeof payload.server_id !== 'string' || typeof payload.fingerprint !== 'string' ||
      !/^[a-f\d]{64}$/i.test(payload.fingerprint) || typeof payload.code !== 'string' ||
      !/^\d{8}$/.test(payload.code)) {
    throw new Error('That QR code is not a valid IoTDesign pairing code.');
  }
  return payload as PairingPayload;
}

export function prioritizeDiscoveredServer(
  savedServerId: string | null,
  services: DiscoveredServer[],
): { type: 'none' } | { type: 'reconnect'; server: DiscoveredServer } |
  { type: 'pair'; server: DiscoveredServer } | { type: 'choose'; servers: DiscoveredServer[] } {
  if (!services.length) return { type: 'none' };
  const remembered = savedServerId ? services.find(server => server.serverId === savedServerId) : undefined;
  if (remembered) return { type: 'reconnect', server: remembered };
  if (services.length === 1) return { type: 'pair', server: services[0] };
  return { type: 'choose', servers: services };
}

export function isSupportedApiVersion(version: number) {
  return Number.isInteger(version) && version === 1;
}
