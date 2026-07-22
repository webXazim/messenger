export type PresenceState = {
  is_online?: boolean | null;
  active_devices?: number;
  last_seen_at?: string | null;
  presence_label?: string | null;
  presence_status?: "active" | "idle" | "offline" | null;
  device_type?: "desktop" | "mobile" | "tablet" | null;
  device_types?: Array<"desktop" | "mobile" | "tablet"> | null;
  presence_visibility?: "public" | "hidden" | null;
};

const PRESENCE_FIELDS = [
  "is_online",
  "active_devices",
  "last_seen_at",
  "presence_label",
  "presence_status",
  "device_type",
  "device_types",
  "presence_visibility",
] as const;

function presenceTime(value?: string | null) {
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function mergeNewestPresence<T extends PresenceState>(current: T | undefined, incoming: T): T {
  if (!current) return incoming;
  if (incoming.presence_visibility === "hidden" || current.presence_visibility === "hidden") return incoming;

  const currentTime = presenceTime(current.last_seen_at);
  const incomingTime = presenceTime(incoming.last_seen_at);
  if (!currentTime || (incomingTime && incomingTime > currentTime)) return incoming;

  const merged = { ...incoming } as T;
  for (const field of PRESENCE_FIELDS) {
    if (current[field] !== undefined) Object.assign(merged, { [field]: current[field] });
  }
  return merged;
}
