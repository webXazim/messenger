export type CameraFacingMode = "user" | "environment";

export function cameraFacingFromTrack(
  track?: Pick<MediaStreamTrack, "getSettings" | "label"> | null,
): CameraFacingMode {
  if (!track) return "user";
  const settings = track.getSettings();
  return settings.facingMode === "environment" || /back|rear|environment|world/i.test(track.label || "")
    ? "environment"
    : "user";
}

export function findPreferredCameraDevice(
  devices: Array<Pick<MediaDeviceInfo, "deviceId" | "label">>,
  targetFacing: CameraFacingMode,
  currentDeviceId: string,
) {
  const targetPattern = targetFacing === "environment"
    ? /back|rear|environment|world/i
    : /front|user|face/i;
  return devices.find((device) => device.deviceId !== currentDeviceId && targetPattern.test(device.label))
    ?? (currentDeviceId ? devices.find((device) => device.deviceId !== currentDeviceId) : null)
    ?? null;
}

export function supportsMobileCameraSwitch({
  facingModeSupported,
  maxTouchPoints,
  userAgent,
}: {
  facingModeSupported: boolean;
  maxTouchPoints: number;
  userAgent: string;
}) {
  return facingModeSupported && (maxTouchPoints > 0 || /Android|iPhone|iPad|iPod|Mobile/i.test(userAgent));
}
