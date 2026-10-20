export type CameraFrame = { uri: string; width: number; height: number };

/** Keep the camera cache file alive until persistence and transport finish. */
export async function processCameraFrame(actions: {
  capture: () => Promise<CameraFrame>;
  active: () => boolean;
  persist: (frame: CameraFrame) => Promise<void>;
  publish: (frame: CameraFrame) => Promise<void>;
  removeTemporary: (uri: string) => Promise<void>;
}) {
  const frame = await actions.capture();
  try {
    if (!actions.active()) return;
    await actions.persist(frame);
    if (actions.active()) await actions.publish(frame);
  } finally {
    await actions.removeTemporary(frame.uri);
  }
}
