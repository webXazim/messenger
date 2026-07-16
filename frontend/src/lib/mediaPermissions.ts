export type DevicePermissionName = "microphone" | "camera";

function isAndroidBrowser() {
  if (typeof navigator === "undefined") return false;
  return /Android/i.test(navigator.userAgent);
}

function isSecureMediaContext() {
  if (typeof window === "undefined") return true;
  return window.isSecureContext || ["localhost", "127.0.0.1"].includes(window.location.hostname);
}

export async function getDevicePermissionState(name: DevicePermissionName): Promise<PermissionState | "unknown"> {
  if (typeof navigator === "undefined" || !navigator.permissions?.query) return "unknown";
  try {
    const status = await navigator.permissions.query({ name } as PermissionDescriptor);
    return status.state;
  } catch {
    return "unknown";
  }
}

export async function getMediaPermissionHint(kind: "voice" | "video") {
  if (!isSecureMediaContext()) {
    return "Camera and microphone need HTTPS. Open the app with https, or use localhost while developing.";
  }
  const microphone = await getDevicePermissionState("microphone");
  const camera = kind === "video" ? await getDevicePermissionState("camera") : "unknown";
  if (microphone === "denied" || camera === "denied") {
    return "Browser permission is blocked. Open site settings from the lock or tune icon, allow microphone and camera, then reload and retry.";
  }
  if (microphone === "prompt" || camera === "prompt") {
    return "The browser should ask for permission. If no popup appears, open site settings from the lock or tune icon and allow microphone and camera.";
  }
  if (isAndroidBrowser()) {
    return "On Android, tap Retry media from this screen after allowing permission. If it still fails, open browser site settings for this page and set Microphone and Camera to Allow.";
  }
  return "Allow microphone and camera for this site, then retry.";
}

export async function getCallMediaErrorMessage(error: unknown, kind: "voice" | "video") {
  const hint = await getMediaPermissionHint(kind);
  if (error instanceof DOMException && ["NotAllowedError", "PermissionDeniedError"].includes(error.name)) {
    return `${error.message || "The browser blocked media access."} ${hint}`;
  }
  const detail = error instanceof Error ? error.message : "Unable to start media devices.";
  return `${detail} ${hint}`;
}

export function buildCallMediaConstraints(kind: "voice" | "video", compactVideo = false): MediaStreamConstraints {
  return {
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    video: kind === "video"
      ? {
          width: { ideal: compactVideo ? 640 : 1280 },
          height: { ideal: compactVideo ? 360 : 720 },
          frameRate: { ideal: compactVideo ? 24 : 30, max: 30 },
          facingMode: "user",
        }
      : false,
  };
}


export async function requestRequiredCallMedia(kind: "voice" | "video") {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("Media devices are not supported in this browser.");
  }
  if (!isSecureMediaContext()) {
    throw new Error("Camera and microphone need HTTPS.");
  }

  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia(buildCallMediaConstraints(kind));
  } catch (error) {
    if (kind !== "video") throw error;
    stream = await navigator.mediaDevices.getUserMedia(buildCallMediaConstraints(kind, true));
  }

  const hasAudio = stream.getAudioTracks().length > 0;
  const hasVideo = stream.getVideoTracks().length > 0;
  if (!hasAudio || (kind === "video" && !hasVideo)) {
    stream.getTracks().forEach((track) => track.stop());
    throw new Error(kind === "video"
      ? "Camera and microphone are required for this video call."
      : "Microphone access is required for this voice call.");
  }
  return stream;
}

export async function preflightCallMedia(kind: "voice" | "video") {
  const stream = await requestRequiredCallMedia(kind);
  stream.getTracks().forEach((track) => track.stop());
}

export async function requestCallMedia(kind: "voice" | "video") {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("Media devices are not supported in this browser.");
  }
  if (!isSecureMediaContext()) {
    throw new Error("Camera and microphone need HTTPS.");
  }
  try {
    return await navigator.mediaDevices.getUserMedia(buildCallMediaConstraints(kind));
  } catch (error) {
    if (kind !== "video") throw error;
    try {
      return await navigator.mediaDevices.getUserMedia(buildCallMediaConstraints(kind, true));
    } catch {
      return navigator.mediaDevices.getUserMedia(buildCallMediaConstraints("voice"));
    }
  }
}
