import { requireNativeModule } from 'expo-modules-core';

type NetworkEvents = {
  onServiceFound: (service: DiscoveredServer) => void;
  onDiscoveryError: (event: { message: string }) => void;
};

export type DiscoveredServer = {
  name: string;
  serverId: string;
  apiVersion: string;
  host: string;
  port: number;
  fingerprint: string;
};

export type PinnedResponse = {
  status: number;
  body: string;
  peerFingerprint: string;
};

type NativeNetwork = {
  startDiscovery(): void;
  stopDiscovery(): void;
  request(
    address: string,
    method: string,
    fingerprint: string,
    headers: Record<string, string>,
    body: string | null,
    filePath: string | null,
    fields: Record<string, string>,
  ): Promise<PinnedResponse>;
  addListener<K extends keyof NetworkEvents>(eventName: K, listener: NetworkEvents[K]): { remove(): void };
};

const native = requireNativeModule<NativeNetwork>('IoTDesignNetwork');

export const startDiscovery = () => native.startDiscovery();
export const stopDiscovery = () => native.stopDiscovery();
export const request = native.request.bind(native);
export const onServiceFound = (listener: NetworkEvents['onServiceFound']) =>
  native.addListener('onServiceFound', listener);
export const onDiscoveryError = (listener: NetworkEvents['onDiscoveryError']) =>
  native.addListener('onDiscoveryError', listener);
