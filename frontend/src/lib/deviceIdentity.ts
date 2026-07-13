const DEVICE_ID_KEY = "messenger.device-id";

export function getStoredDeviceId() {
  return localStorage.getItem(DEVICE_ID_KEY) || "";
}

export function getOrCreateDeviceId() {
  const existing = getStoredDeviceId();
  if (existing) return existing;

  const generated = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? `device-${crypto.randomUUID()}`
    : `device-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;

  localStorage.setItem(DEVICE_ID_KEY, generated);
  return generated;
}
