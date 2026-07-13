export type PersonPresentation = {
  id?: string | number | null;
  username?: string | null;
  display_name?: string | null;
  full_name?: string | null;
  first_name?: string | null;
  last_name?: string | null;
  avatar?: string | null;
  is_online?: boolean | null;
  last_seen_at?: string | null;
  presence_label?: string | null;
  presence_visibility?: "public" | "hidden" | null;
};

export function personDisplayName(person?: PersonPresentation | null, fallback = "User") {
  if (!person) return fallback;
  const fullName = [person.first_name, person.last_name].filter(Boolean).join(" ").trim();
  return String(person.display_name || person.full_name || fullName || person.username || fallback).trim() || fallback;
}

export function personInitials(person?: PersonPresentation | null, fallback = "U") {
  const label = personDisplayName(person, fallback);
  const parts = label.split(/\s+/).filter(Boolean).slice(0, 2);
  return (parts.map((part) => part[0]).join("") || label.slice(0, 2) || fallback).toUpperCase();
}

export function formatLastSeen(value?: string | null) {
  if (!value) return "Offline";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Offline";

  const diffSeconds = Math.round((date.getTime() - Date.now()) / 1000);
  const absoluteSeconds = Math.abs(diffSeconds);
  const relative = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

  if (absoluteSeconds < 60) return "Last active just now";
  if (absoluteSeconds < 3600) return `Last active ${relative.format(Math.round(diffSeconds / 60), "minute")}`;
  if (absoluteSeconds < 86_400) return `Last active ${relative.format(Math.round(diffSeconds / 3600), "hour")}`;
  if (absoluteSeconds < 604_800) return `Last active ${relative.format(Math.round(diffSeconds / 86_400), "day")}`;
  return `Last active ${date.toLocaleDateString([], { month: "short", day: "numeric" })}`;
}

export function personPresenceText(person?: PersonPresentation | null) {
  if (person?.is_online) return "Active now";
  if (person?.presence_visibility === "hidden") return "Offline";
  if (person?.last_seen_at) return formatLastSeen(person.last_seen_at);
  const label = String(person?.presence_label || "").trim().toLowerCase();
  if (label && label !== "online") return label.charAt(0).toUpperCase() + label.slice(1);
  return "Offline";
}
