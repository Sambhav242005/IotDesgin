import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator, AppState, Image, KeyboardAvoidingView, Platform, Pressable,
  SafeAreaView, ScrollView, StyleSheet, Text, TextInput, View,
} from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as FileSystem from 'expo-file-system/legacy';
import { ImageManipulator, SaveFormat } from 'expo-image-manipulator';
import * as SecureStore from 'expo-secure-store';
import NetInfo from '@react-native-community/netinfo';
import { StatusBar } from 'expo-status-bar';
import {
  onDiscoveryError, onServiceFound, request, startDiscovery, stopDiscovery,
  type DiscoveredServer,
} from './modules/iotdesign-network/src';
import { isSupportedApiVersion, normalizeAddress, parsePairing, prioritizeDiscoveredServer } from './src/protocol';

type Server = { id: string; name: string; address: string; fingerprint: string };
type Frame = { id: string; uri: string; timestamp: string; width: number; height: number; scanId: string };
type SavedState = { server: Server | null; token: string | null; clientName: string; scanId: string | null; pending: Frame[] };
type ApiResult<T> = { value: T; peerFingerprint: string };

const STORE_KEY = 'iotdesign.saved.v1';
const FRAME_DIR = `${FileSystem.documentDirectory}iotdesign/frames/`;
const blank: SavedState = { server: null, token: null, clientName: 'My phone', scanId: null, pending: [] };

function serviceToServer(service: DiscoveredServer): Server {
  return {
    id: service.serverId, name: service.name,
    address: `https://${service.host.includes(':') ? `[${service.host}]` : service.host}:${service.port}`,
    fingerprint: service.fingerprint,
  };
}

async function readSaved(): Promise<SavedState> {
  try {
    const value = await SecureStore.getItemAsync(STORE_KEY);
    return value ? { ...blank, ...JSON.parse(value) } : blank;
  } catch { return blank; }
}

async function saveSaved(value: SavedState) {
  await SecureStore.setItemAsync(STORE_KEY, JSON.stringify(value));
}

function errorText(error: unknown) {
  const value = error instanceof Error ? error.message : String(error);
  if (/fingerprint|certificate/i.test(value)) return 'The PC certificate changed. Check the pairing QR code before trusting this server.';
  if (/network|connect|timed out|failed to fetch/i.test(value)) return 'PC is not reachable. Check Wi-Fi and Windows Firewall, then retry.';
  return value;
}

export default function App() {
  const [permission, requestPermission] = useCameraPermissions();
  const [saved, setSaved] = useState<SavedState>(blank);
  const savedRef = useRef(saved);
  const [screen, setScreen] = useState<'loading' | 'connect' | 'pair' | 'home' | 'capture' | 'qr'>('loading');
  const screenRef = useRef(screen);
  screenRef.current = screen;
  const [connection, setConnection] = useState('Starting…');
  const [message, setMessage] = useState('');
  const [addressInput, setAddressInput] = useState('');
  const [pairingCode, setPairingCode] = useState('');
  const [candidate, setCandidate] = useState<Server | null>(null);
  const [discoveredServers, setDiscoveredServers] = useState<Server[]>([]);
  const [verified, setVerified] = useState<Server | null>(null);
  const [fingerprintConfirmed, setFingerprintConfirmed] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [scanName, setScanName] = useState('Room scan');
  const [capturing, setCapturing] = useState(false);
  const [cameraReady, setCameraReady] = useState(false);
  const [frames, setFrames] = useState<Frame[]>([]);
  const [uploaded, setUploaded] = useState(0);
  const [reconstructing, setReconstructing] = useState(false);
  const [pictureSize, setPictureSize] = useState<string | undefined>();
  const [cameraError, setCameraError] = useState('');
  const camera = useRef<CameraView>(null);
  const captureLock = useRef(false);
  const flushLock = useRef(false);
  const pendingFlush = useRef<(() => Promise<void>) | null>(null);
  const qrScanLock = useRef(false);
  const discoveryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const captureNumber = useRef(0);
  const multiCandidates = useRef<Record<string, DiscoveredServer>>({});

  const updateSaved = useCallback(async (value: SavedState) => {
    savedRef.current = value;
    setSaved(value);
    await saveSaved(value);
  }, []);

  const call = useCallback(async <T,>(
    method: string, path: string,
    options: { body?: unknown; filePath?: string; fields?: Record<string, string>; token?: string | null; server?: Server; fingerprint?: string } = {},
  ): Promise<ApiResult<T>> => {
    const server = options.server ?? savedRef.current.server;
    if (!server) throw new Error('Select a PC server first.');
    const headers: Record<string, string> = {};
    const token = options.token === undefined ? savedRef.current.token : options.token;
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await request(
      `${server.address}${path}`, method, options.fingerprint ?? server.fingerprint,
      headers, options.body === undefined ? null : JSON.stringify(options.body),
      options.filePath ?? null, options.fields ?? {},
    );
    if (response.status < 200 || response.status >= 300) {
      let detail = `PC returned HTTP ${response.status}`;
      try { detail = JSON.parse(response.body).detail ?? detail; } catch { /* keep status */ }
      throw new Error(detail);
    }
    return { value: response.body ? JSON.parse(response.body) as T : ({} as T), peerFingerprint: response.peerFingerprint };
  }, []);

  const checkServer = useCallback(async (server: Server, token?: string | null, allowFirstTrust = false) => {
    setConnection(`Checking ${server.name}…`);
    const result = await call<{ server_id: string; name: string; api_version: number; fingerprint: string; ready: boolean }>(
      'GET', '/api/health', { server, token: token ?? null, fingerprint: allowFirstTrust ? '' : server.fingerprint },
    );
    const health = result.value;
    if (server.id && health.server_id !== server.id) throw new Error('Server identity does not match the saved pairing.');
    if (!isSupportedApiVersion(health.api_version)) throw new Error(`This app needs API version 1; the PC reports version ${health.api_version}.`);
    if (!result.peerFingerprint || health.fingerprint.toLowerCase() !== result.peerFingerprint.toLowerCase()) {
      throw new Error('The server certificate fingerprint could not be verified.');
    }
    return { ...server, id: health.server_id, name: health.name, fingerprint: health.fingerprint };
  }, [call]);

  const reconnect = useCallback(async (state = savedRef.current) => {
    if (!state.server) return false;
    try {
      const server = await checkServer(state.server, state.token);
      if (state.token) {
        await call('GET', '/api/scans', { server, token: state.token });
        await updateSaved({ ...state, server });
        setConnection(`Connected · ${server.name}`);
        setScreen('home');
        setMessage('');
        void pendingFlush.current?.();
        return true;
      }
      await updateSaved({ ...state, server });
      setVerified(server);
      setCandidate(server);
      setFingerprintConfirmed(false);
      setScreen('pair');
      setConnection('PC found · enter the pairing code shown on it');
      return true;
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      if (state.token && /invalid_credentials|authentication_required/i.test(detail)) {
        const unpaired = { ...state, token: null };
        await updateSaved(unpaired);
        setCandidate(state.server); setVerified(state.server); setFingerprintConfirmed(false);
        setScreen('pair'); setConnection('Pairing was revoked · pair this phone again');
        setMessage('Enter a fresh pairing code from the PC capture station.');
        return false;
      }
      setConnection('Searching for your PC…');
      setMessage(errorText(error));
      return false;
    }
  }, [call, checkServer, updateSaved]);

  const acceptService = useCallback((service: DiscoveredServer) => {
    if (service.apiVersion !== '1' || !/^[a-f\d]{64}$/i.test(service.fingerprint)) {
      setMessage(`${service.name || 'A nearby PC'} advertises an unsupported server version or missing certificate fingerprint.`);
      return;
    }
    multiCandidates.current[service.serverId] = service;
  }, []);

  const beginDiscovery = useCallback(() => {
    multiCandidates.current = {};
    setDiscoveredServers([]);
    setDiscovering(true);
    setMessage('');
    setScreen(current => current === 'home' || current === 'capture' ? current : 'connect');
    startDiscovery();
    if (discoveryTimer.current) clearTimeout(discoveryTimer.current);
    discoveryTimer.current = setTimeout(() => {
      stopDiscovery();
      setDiscovering(false);
      const services = Object.values(multiCandidates.current);
      const action = prioritizeDiscoveredServer(savedRef.current.server?.id ?? null, services);
      if (action.type === 'reconnect') {
        const remembered = savedRef.current.server;
        const discovered = serviceToServer(action.server);
        if (remembered && remembered.fingerprint !== discovered.fingerprint) {
          setCandidate(discovered); setVerified(null); setFingerprintConfirmed(false);
          setAddressInput(discovered.address); setScreen('pair');
          setConnection('The PC certificate changed · compare it and pair again');
          setMessage('The saved certificate no longer matches this PC. Verify the new fingerprint on the desktop before pairing again.');
        } else if (remembered) void reconnect({ ...savedRef.current, server: discovered });
        return;
      }
      if (action.type === 'pair') {
        const server = serviceToServer(action.server);
        setCandidate(server); setVerified(null); setAddressInput(server.address); setFingerprintConfirmed(false);
        if (screenRef.current !== 'home' && screenRef.current !== 'capture') {
          setScreen('pair'); setConnection(`Found ${server.name} · enter the pairing code shown on the PC`);
        }
        return;
      }
      if (action.type === 'choose') {
        setDiscoveredServers(action.servers.map(serviceToServer));
        if (screenRef.current !== 'home' && screenRef.current !== 'capture') {
          setScreen('connect'); setConnection(`Found ${action.servers.length} PCs · choose one to pair`);
          setMessage(`Found ${action.servers.length} IoTDesign PCs. Select the one you want to pair.`);
        }
        return;
      }
      if (screenRef.current !== 'home' && screenRef.current !== 'capture') setConnection('No PC found · enter its address below');
    }, 8000);
  }, [reconnect]);

  useEffect(() => {
    const found = onServiceFound(service => { void acceptService(service); });
    const discoveryError = onDiscoveryError(event => setMessage(event.message));
    void readSaved().then(async state => {
      await updateSaved(state);
      setFrames(state.pending);
      if (state.server) void reconnect(state);
      else setConnection('Looking for your PC…');
      beginDiscovery();
    });
    const network = NetInfo.addEventListener(state => {
      if (state.isConnected && savedRef.current.server) { void reconnect(); beginDiscovery(); }
    });
    const appState = AppState.addEventListener('change', state => {
      if (state === 'active' && savedRef.current.server) { void reconnect(); beginDiscovery(); }
    });
    return () => {
      found.remove(); discoveryError.remove(); network(); appState.remove(); stopDiscovery();
      if (discoveryTimer.current) clearTimeout(discoveryTimer.current);
    };
  }, [acceptService, beginDiscovery, reconnect, updateSaved]);

  const pair = useCallback(async (server: Server, code: string, qrFingerprintConfirmed = false) => {
    if (!/^\d{8}$/.test(code)) throw new Error('Enter the 8 digit code shown on the PC.');
    if (!qrFingerprintConfirmed && !fingerprintConfirmed) throw new Error('Compare the fingerprint with the PC capture station and confirm it before pairing.');
    const trusted = qrFingerprintConfirmed ? server : verified?.id === server.id ? verified : await checkServer(server);
    const response = await call<{ token: string; client_id: string; server_id: string }>(
      'POST', '/api/pair', {
        server: trusted, token: null, body: { code, name: savedRef.current.clientName },
        fingerprint: trusted.fingerprint,
      },
    );
    if (response.value.server_id !== trusted.id) throw new Error('The PC returned a different server identity.');
    await updateSaved({ ...savedRef.current, server: trusted, token: response.value.token });
    setConnection(`Connected · ${trusted.name}`);
    setMessage(''); setScreen('home'); setCandidate(null); setVerified(null); stopDiscovery();
    void pendingFlush.current?.();
  }, [call, checkServer, fingerprintConfirmed, updateSaved, verified]);

  const inspectManualAddress = useCallback(async () => {
    try {
      const address = normalizeAddress(addressInput);
      const server = { id: '', name: 'IoTDesign PC', address, fingerprint: '' };
      const response = await request(`${address}/api/health`, 'GET', '', {}, null, null, {});
      if (response.status !== 200) throw new Error(`PC returned HTTP ${response.status}.`);
      const health = JSON.parse(response.body) as { server_id: string; name: string; api_version: number; fingerprint: string };
      if (!isSupportedApiVersion(health.api_version)) throw new Error(`This app needs API version 1; the PC reports version ${health.api_version}.`);
      if (!response.peerFingerprint || response.peerFingerprint.toLowerCase() !== health.fingerprint.toLowerCase()) {
        throw new Error('The PC certificate fingerprint did not match its health response.');
      }
      const next = { ...server, id: health.server_id, name: health.name, fingerprint: health.fingerprint };
      setVerified(next); setCandidate(next); setPairingCode(''); setConnection(`Found ${health.name}`);
      setFingerprintConfirmed(false);
      setMessage(`Certificate verified for this connection: ${health.fingerprint.match(/.{1,4}/g)?.join(' ')}`);
      setScreen('pair');
    } catch (error) { setMessage(errorText(error)); }
  }, [addressInput]);

  const scanPairingQr = useCallback(async (data: string) => {
    if (qrScanLock.current) return;
    qrScanLock.current = true;
    try {
      const payload = parsePairing(data);
      const server: Server = {
        id: payload.server_id, name: 'IoTDesign PC', address: normalizeAddress(payload.address),
        fingerprint: payload.fingerprint,
      };
      const trusted = await checkServer(server);
      setVerified(trusted); setCandidate(trusted); setPairingCode(payload.code); setScreen('pair');
      await pair(trusted, payload.code, true);
    } catch (error) { setScreen('connect'); setMessage(errorText(error)); }
    finally { qrScanLock.current = false; }
  }, [checkServer, pair]);

  const flushQueue = useCallback(async () => {
    if (flushLock.current || !savedRef.current.server || !savedRef.current.token) return;
    flushLock.current = true;
    try {
      while (savedRef.current.pending.length) {
        const frame = savedRef.current.pending[0];
        await call('PUT', `/api/scans/${frame.scanId}/frames/${frame.id}`, {
          filePath: frame.uri,
          fields: { timestamp: frame.timestamp, width: String(frame.width), height: String(frame.height) },
        });
        const next = { ...savedRef.current, pending: savedRef.current.pending.slice(1) };
        await updateSaved(next); setFrames(next.pending); setUploaded(value => value + 1);
        await FileSystem.deleteAsync(frame.uri, { idempotent: true }).catch(() => undefined);
      }
    } catch (error) {
      setConnection('Disconnected · keeping captured frames on this phone'); setMessage(errorText(error));
    } finally { flushLock.current = false; }
  }, [call, updateSaved]);
  pendingFlush.current = flushQueue;

  const captureOne = useCallback(async () => {
    if (captureLock.current || !cameraReady || !camera.current || !savedRef.current.scanId) return;
    if (captureNumber.current >= 30) {
      setCapturing(false); setMessage('This scan reached the 30 frame limit. Finish it to reconstruct.'); return;
    }
    captureLock.current = true;
    try {
      const picture = await camera.current.takePictureAsync({ quality: 0.82, exif: false });
      const longestEdge = Math.max(picture.width, picture.height);
      const normalized = longestEdge > 1280
        ? await (await ImageManipulator.manipulate(picture.uri)
          .resize(picture.width >= picture.height ? { width: 1280 } : { height: 1280 }))
          .renderAsync().then(image => image.saveAsync({ compress: 0.82, format: SaveFormat.JPEG }))
        : picture;
      const id = `${Date.now()}-${++captureNumber.current}`;
      await FileSystem.makeDirectoryAsync(FRAME_DIR, { intermediates: true });
      const uri = `${FRAME_DIR}${id}.jpg`;
      await FileSystem.copyAsync({ from: normalized.uri, to: uri });
      const frame: Frame = {
        id, uri, timestamp: new Date().toISOString(), width: normalized.width, height: normalized.height,
        scanId: savedRef.current.scanId,
      };
      const next = { ...savedRef.current, pending: [...savedRef.current.pending, frame] };
      await updateSaved(next); setFrames(next.pending); void flushQueue();
    } catch (error) { setMessage(`Capture failed: ${errorText(error)}`); }
    finally { captureLock.current = false; }
  }, [cameraReady, flushQueue, updateSaved]);

  useEffect(() => {
    if (!capturing) return;
    const timer = setInterval(() => { void captureOne(); }, 2000);
    return () => clearInterval(timer);
  }, [captureOne, capturing]);

  const startScan = useCallback(async () => {
    try {
      if (savedRef.current.scanId) throw new Error('A scan is still in progress. Resume it or finish it before starting another.');
      if (!permission?.granted) {
        const granted = await requestPermission();
        if (!granted.granted) throw new Error('Allow camera access in your phone settings to scan a room.');
      }
      const response = await call<{ id: string }>('POST', '/api/scans', { body: { name: scanName.trim() || 'Room scan' } });
      const next = { ...savedRef.current, scanId: response.value.id, pending: [] };
      await updateSaved(next); setFrames([]); setUploaded(0); captureNumber.current = 0;
      setCameraReady(false); setCameraError(''); setScreen('capture'); setCapturing(true);
      setMessage('Move slowly around the room. Keep each view overlapping the last.');
    } catch (error) { setMessage(errorText(error)); }
  }, [call, permission?.granted, requestPermission, scanName, updateSaved]);

  const finishScan = useCallback(async () => {
    setCapturing(false);
    await flushQueue();
    const current = savedRef.current;
    if (current.pending.some(frame => frame.scanId === current.scanId)) {
      setMessage('Waiting for the remaining photos to upload. Keep the app open and try Finish again.'); return;
    }
    try {
      const scan = await call<{ frames: Frame[] }>('GET', `/api/scans/${current.scanId}`);
      const count = scan.value.frames.length;
      if (count < 8) { setMessage(`This scan has ${count} uploaded photos. Capture at least 8 before reconstructing.`); return; }
      const frameCount = count >= 20 ? 20 : count >= 12 ? 12 : 8;
      setReconstructing(true);
      await call('POST', `/api/scans/${current.scanId}/reconstruct`, { body: { frame_count: frameCount } });
      setMessage(`Reconstruction queued with ${frameCount} images. Check progress on the PC capture station.`);
      await updateSaved({ ...current, scanId: null }); setScreen('home');
    } catch (error) { setMessage(errorText(error)); }
    finally { setReconstructing(false); }
  }, [call, flushQueue, updateSaved]);

  const forgetServer = useCallback(async () => {
    const next = { ...savedRef.current, server: null, token: null };
    await updateSaved(next); setVerified(null); setCandidate(null); setScreen('connect');
    setConnection('Looking for an IoTDesign PC…'); setMessage(''); beginDiscovery();
  }, [beginDiscovery, updateSaved]);

  const onCameraReady = useCallback(async () => {
    setCameraReady(true);
    try {
      const sizes = await camera.current?.getAvailablePictureSizesAsync();
      const preferred = sizes?.find(size => {
        const match = /^(\d+)x(\d+)$/.exec(size);
        return match && Number(match[1]) <= 1600 && Number(match[1]) >= 1280 && Number(match[2]) >= 720;
      });
      if (preferred) setPictureSize(preferred);
    } catch { /* use the camera's supported default */ }
  }, []);

  const selectCandidate = (server: Server) => {
    setCandidate(server); setAddressInput(server.address); setVerified(null); setFingerprintConfirmed(false);
    setConnection(`Found ${server.name} · enter the pairing code shown on the PC`);
    setScreen('pair');
  };

  return (
    <SafeAreaView style={styles.safe}>
      <StatusBar style="light" />
      <KeyboardAvoidingView style={styles.safe} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <View style={styles.header}>
          <View><Text style={styles.eyebrow}>IOTDESIGN · LOCAL ROOM SCANNER</Text><Text style={styles.brand}>Room capture</Text></View>
          <View style={styles.statusDot} />
        </View>

        {screen === 'loading' ? <View style={styles.center}><ActivityIndicator color="#c8ef92" /><Text style={styles.body}>{connection}</Text></View> : null}

        {screen === 'connect' ? <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <Text style={styles.step}>01 / PC CONNECTION</Text>
          <Text style={styles.title}>Find your PC.</Text>
          <Text style={styles.body}>{connection}. Your phone and PC must be on the same Wi-Fi network.</Text>
          <View style={styles.card}>
            <View style={styles.row}><ActivityIndicator color="#c8ef92" /><Text style={styles.cardTitle}>{discovering ? 'Searching nearby…' : 'Ready to search'}</Text></View>
            <Text style={styles.muted}>The PC must be running the IoTDesign server. Discovery checks for up to 8 seconds.</Text>
            <Pressable style={styles.secondaryButton} onPress={beginDiscovery}><Text style={styles.secondaryText}>Search again</Text></Pressable>
          </View>
          {discoveredServers.length > 1 ? <View style={styles.card}>
            <Text style={styles.cardTitle}>Choose your PC</Text>
            {discoveredServers.map(server => <Pressable key={server.id} style={styles.serverRow} onPress={() => selectCandidate(server)}>
              <Text style={styles.secondaryText}>{server.name}</Text><Text style={styles.muted}>{server.address}</Text>
            </Pressable>)}
          </View> : null}
          <Text style={styles.step}>MANUAL CONNECTION</Text>
          <Text style={styles.body}>If discovery is blocked by your Wi-Fi, enter the address shown on the PC.</Text>
          <TextInput style={styles.input} value={addressInput} onChangeText={setAddressInput} placeholder="192.168.1.20:8443" placeholderTextColor="#718082" autoCapitalize="none" autoCorrect={false} keyboardType="url" />
          <Pressable style={styles.primaryButton} onPress={() => void inspectManualAddress()}><Text style={styles.primaryText}>Check PC address</Text></Pressable>
          <Pressable style={styles.qrButton} onPress={() => {
            void (async () => {
              const result = permission?.granted ? permission : await requestPermission();
              if (!result.granted) { setMessage('Allow camera access to scan the pairing QR code.'); return; }
              setScreen('qr'); setMessage('Point your camera at the pairing QR on the PC.');
            })();
          }}><Text style={styles.secondaryText}>Scan pairing QR code</Text></Pressable>
          {message ? <Text style={styles.notice}>{message}</Text> : null}
          <Text style={styles.footnote}>If the PC address changes, automatic discovery will find it again. Pairing credentials stay in secure phone storage.</Text>
        </ScrollView> : null}

        {screen === 'pair' && candidate ? <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <Text style={styles.step}>02 / PAIR THIS PHONE</Text>
          <Text style={styles.title}>Connect to {candidate.name}.</Text>
          <Text style={styles.body}>Enter the 8 digit code shown on the PC capture station. Compare the certificate fingerprint before sending the code.</Text>
          <View style={styles.card}><Text style={styles.muted}>PC SERVER</Text><Text style={styles.cardTitle}>{candidate.name}</Text><Text style={styles.muted}>{candidate.address}</Text></View>
          <View style={styles.card}>
            <Text style={styles.muted}>CERTIFICATE SHA-256</Text>
            <Text selectable style={styles.fingerprint}>{candidate.fingerprint.match(/.{1,4}/g)?.join(' ') ?? candidate.fingerprint}</Text>
            <Pressable style={styles.confirmRow} onPress={() => setFingerprintConfirmed(value => !value)}>
              <Text style={styles.checkBox}>{fingerprintConfirmed ? '✓' : '○'}</Text>
              <Text style={styles.muted}>I compared this fingerprint with the one shown on the PC.</Text>
            </Pressable>
          </View>
          <TextInput style={[styles.input, styles.codeInput]} value={pairingCode} onChangeText={value => setPairingCode(value.replace(/\D/g, '').slice(0, 8))} placeholder="00000000" placeholderTextColor="#718082" keyboardType="number-pad" maxLength={8} />
          <Pressable style={[styles.primaryButton, !fingerprintConfirmed && styles.disabledButton]} onPress={() => void pair(candidate, pairingCode).catch(error => setMessage(errorText(error)))}><Text style={styles.primaryText}>Pair and save this PC</Text></Pressable>
          <Pressable style={styles.qrButton} onPress={() => setScreen('connect')}><Text style={styles.secondaryText}>Change server</Text></Pressable>
          {message ? <Text style={styles.notice}>{message}</Text> : null}
        </ScrollView> : null}

        {screen === 'qr' ? <View style={styles.qrCamera}>
          <CameraView style={StyleSheet.absoluteFill} facing="back" barcodeScannerSettings={{ barcodeTypes: ['qr'] }} onBarcodeScanned={event => { void scanPairingQr(event.data); }} />
          <View style={styles.qrOverlay}><Text style={styles.title}>Scan PC pairing QR</Text><Text style={styles.body}>Hold the code inside the camera view.</Text><Pressable style={styles.primaryButton} onPress={() => setScreen('connect')}><Text style={styles.primaryText}>Cancel</Text></Pressable></View>
        </View> : null}

        {screen === 'home' ? <ScrollView contentContainerStyle={styles.content}>
          <Text style={styles.step}>03 / READY TO SCAN</Text>
          <Text style={styles.title}>Connected.</Text>
          <View style={styles.card}><Text style={styles.muted}>SAVED PC</Text><Text style={styles.cardTitle}>{saved.server?.name ?? 'IoTDesign PC'}</Text><Text style={styles.muted}>{saved.server?.address}</Text><Text style={styles.connected}>{connection}</Text></View>
          <Text style={styles.label}>ROOM NAME</Text>
          <TextInput style={styles.input} value={scanName} onChangeText={setScanName} placeholder="Room scan" placeholderTextColor="#718082" />
          <Pressable style={styles.primaryButton} onPress={() => void startScan()}><Text style={styles.primaryText}>Start room scan</Text></Pressable>
          {saved.pending.length ? <View style={styles.card}><Text style={styles.cardTitle}>{saved.pending.length} saved photos waiting to upload</Text><Pressable style={styles.secondaryButton} onPress={() => void flushQueue()}><Text style={styles.secondaryText}>Retry upload</Text></Pressable></View> : null}
          {saved.scanId ? <View style={styles.card}>
            <Text style={styles.cardTitle}>An unfinished scan is saved</Text>
            <Text style={styles.muted}>Resume to keep capturing or finish uploading its saved photos.</Text>
            <Pressable style={styles.secondaryButton} onPress={() => {
              void call<{ frames: Frame[] }>('GET', `/api/scans/${saved.scanId}`).then(async result => {
                setUploaded(result.value.frames.length);
                captureNumber.current = Math.min(result.value.frames.length + saved.pending.length, 30);
                setFrames(saved.pending); setCameraReady(false); setScreen('capture'); setCapturing(true);
                if (saved.pending.length) void flushQueue();
              }).catch(error => setMessage(errorText(error)));
            }}><Text style={styles.secondaryText}>Resume saved scan</Text></Pressable>
          </View> : null}
          <Pressable style={styles.qrButton} onPress={() => void forgetServer()}><Text style={styles.secondaryText}>Change or forget PC</Text></Pressable>
          {message ? <Text style={styles.notice}>{message}</Text> : null}
        </ScrollView> : null}

        {screen === 'capture' ? <View style={styles.captureScreen}>
          <View style={styles.cameraFrame}>
            <CameraView ref={camera} style={StyleSheet.absoluteFill} facing="back" pictureSize={pictureSize} onCameraReady={() => void onCameraReady()} onMountError={event => setCameraError(event.message)} />
            <View style={styles.cameraHud}><Text style={styles.cameraHudText}>{capturing ? 'CAPTURING · ONE FRAME / 2 SEC' : 'PAUSED'}</Text><Text style={styles.cameraHudText}>{uploaded} uploaded · {frames.length} waiting</Text></View>
            <View pointerEvents="none" style={styles.guide}><View style={styles.guideCorner} /><View style={[styles.guideCorner, styles.topRight]} /><View style={[styles.guideCorner, styles.bottomLeft]} /><View style={[styles.guideCorner, styles.bottomRight]} /></View>
          </View>
          <View style={styles.captureControls}>
            <Text style={styles.step}>ROOM SCAN · {Math.min(uploaded + frames.length, 30)}/30 FRAMES</Text>
            <Text style={styles.body}>Walk slowly. Overlap each view and keep the same objects visible as you turn.</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.thumbs}>
              {frames.map(frame => <Image key={frame.id} source={{ uri: frame.uri }} style={styles.thumb} />)}
              {Array.from({ length: Math.min(uploaded, 8) }).map((_, index) => <View key={`uploaded-${index}`} style={[styles.thumb, styles.uploadedThumb]}><Text style={styles.uploadedCheck}>✓</Text></View>)}
            </ScrollView>
            <View style={styles.controlsRow}>
              <Pressable style={styles.secondaryButton} onPress={() => setCapturing(value => !value)}><Text style={styles.secondaryText}>{capturing ? 'Pause' : 'Resume'}</Text></Pressable>
              <Pressable style={styles.secondaryButton} onPress={() => void captureOne()}><Text style={styles.secondaryText}>Capture now</Text></Pressable>
              <Pressable style={[styles.primaryButton, styles.finishButton]} onPress={() => void finishScan()} disabled={reconstructing}><Text style={styles.primaryText}>{reconstructing ? 'Queuing…' : 'Finish scan'}</Text></Pressable>
            </View>
            {cameraError || message ? <Text style={styles.notice}>{cameraError || message}</Text> : null}
          </View>
        </View> : null}
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#101b1d' },
  header: { height: 72, paddingHorizontal: 22, borderBottomWidth: 1, borderBottomColor: '#293638', flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  eyebrow: { color: '#adbfbb', fontSize: 10, letterSpacing: 1.6, fontWeight: '700' },
  brand: { color: '#f0f3eb', fontSize: 19, fontWeight: '600', marginTop: 4 },
  statusDot: { width: 9, height: 9, borderRadius: 8, backgroundColor: '#c8ef92' },
  content: { paddingHorizontal: 22, paddingTop: 30, paddingBottom: 48, gap: 14 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 16 },
  step: { color: '#c8ef92', fontSize: 10, letterSpacing: 1.7, fontWeight: '700', marginTop: 4 },
  title: { color: '#f0f3eb', fontSize: 31, lineHeight: 38, fontWeight: '600', letterSpacing: -0.6 },
  body: { color: '#a8b6b4', fontSize: 15, lineHeight: 23 },
  muted: { color: '#8c9d9d', fontSize: 12, lineHeight: 19, marginTop: 4 },
  card: { backgroundColor: '#1a282a', borderWidth: 1, borderColor: '#2b3a3a', padding: 17, gap: 8, marginVertical: 4 },
  cardTitle: { color: '#edf1e8', fontSize: 17, fontWeight: '600' },
  row: { flexDirection: 'row', gap: 12, alignItems: 'center' },
  input: { color: '#eef2e9', fontSize: 16, borderWidth: 1, borderColor: '#39494a', backgroundColor: '#172426', paddingHorizontal: 14, paddingVertical: 14, borderRadius: 8, marginTop: 2 },
  codeInput: { fontSize: 25, textAlign: 'center', letterSpacing: 7 },
  label: { color: '#a8b6b4', fontSize: 11, letterSpacing: 1.1, fontWeight: '700', marginTop: 10 },
  primaryButton: { backgroundColor: '#c8ef92', minHeight: 50, borderRadius: 8, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 16, marginTop: 4 },
  primaryText: { color: '#14221b', fontSize: 15, fontWeight: '700' },
  secondaryButton: { backgroundColor: '#273638', minHeight: 43, borderRadius: 8, paddingHorizontal: 14, alignItems: 'center', justifyContent: 'center', marginTop: 6 },
  secondaryText: { color: '#e1ead8', fontWeight: '600', fontSize: 14 },
  qrButton: { minHeight: 44, alignItems: 'center', justifyContent: 'center', padding: 8 },
  notice: { color: '#ffd2a1', fontSize: 13, lineHeight: 19, marginTop: 8 },
  footnote: { color: '#718082', fontSize: 12, lineHeight: 18, marginTop: 12 },
  connected: { color: '#c8ef92', fontSize: 12, marginTop: 6 },
  fingerprint: { color: '#d9e7d7', fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace', fontSize: 12, lineHeight: 20, marginTop: 4 },
  confirmRow: { flexDirection: 'row', alignItems: 'center', gap: 9, marginTop: 10 },
  checkBox: { color: '#c8ef92', fontSize: 20 },
  disabledButton: { opacity: 0.45 },
  serverRow: { borderTopWidth: 1, borderTopColor: '#304143', paddingVertical: 12, gap: 4 },
  qrCamera: { flex: 1, overflow: 'hidden' },
  qrOverlay: { position: 'absolute', left: 20, right: 20, bottom: 28, backgroundColor: '#101b1de8', padding: 18, gap: 8, borderRadius: 12 },
  captureScreen: { flex: 1 },
  cameraFrame: { flex: 1, minHeight: 260, overflow: 'hidden', backgroundColor: '#0a1112' },
  cameraHud: { position: 'absolute', top: 13, left: 13, right: 13, flexDirection: 'row', justifyContent: 'space-between' },
  cameraHudText: { color: 'white', backgroundColor: '#101b1dcc', paddingHorizontal: 10, paddingVertical: 7, borderRadius: 5, fontSize: 10, fontWeight: '700', letterSpacing: 0.6 },
  guide: { position: 'absolute', left: '9%', right: '9%', top: '16%', bottom: '16%' },
  guideCorner: { position: 'absolute', top: 0, left: 0, width: 25, height: 25, borderTopWidth: 2, borderLeftWidth: 2, borderColor: '#d4f29e' },
  topRight: { left: undefined, right: 0, borderLeftWidth: 0, borderRightWidth: 2 },
  bottomLeft: { top: undefined, bottom: 0, borderTopWidth: 0, borderBottomWidth: 2 },
  bottomRight: { top: undefined, left: undefined, right: 0, bottom: 0, borderTopWidth: 0, borderLeftWidth: 0, borderRightWidth: 2, borderBottomWidth: 2 },
  captureControls: { paddingHorizontal: 18, paddingTop: 13, paddingBottom: 18, gap: 8, backgroundColor: '#101b1d' },
  thumbs: { maxHeight: 64, marginVertical: 3 },
  thumb: { width: 62, height: 52, borderRadius: 6, backgroundColor: '#28393a', marginRight: 7 },
  uploadedThumb: { alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: '#547c3d' },
  uploadedCheck: { color: '#c8ef92', fontSize: 22 },
  controlsRow: { flexDirection: 'row', alignItems: 'center', gap: 7, justifyContent: 'space-between' },
  finishButton: { flex: 1 },
});
