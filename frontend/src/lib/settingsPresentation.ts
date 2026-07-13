import type { SessionInfo } from "../types/auth";
import type { NotificationPreferences } from "../types/chat";

export type CallQualityPreference = NotificationPreferences["call_quality_preference"];

export const CALL_QUALITY_OPTIONS: Array<{
  value: CallQualityPreference;
  label: string;
  description: string;
}> = [
  {
    value: "auto",
    label: "Automatic",
    description: "Adjust quality as your connection changes.",
  },
  {
    value: "low",
    label: "Data saver",
    description: "Use less mobile data on slower connections.",
  },
  {
    value: "mid",
    label: "Balanced",
    description: "Balance clarity, stability, and data use.",
  },
  {
    value: "clear",
    label: "Best quality",
    description: "Prefer the clearest audio and video on fast networks.",
  },
];

function browserName(userAgent: string) {
  if (/Edg\//i.test(userAgent)) return "Microsoft Edge";
  if (/OPR\//i.test(userAgent)) return "Opera";
  if (/Firefox\//i.test(userAgent)) return "Firefox";
  if (/CriOS\//i.test(userAgent)) return "Chrome";
  if (/Chrome\//i.test(userAgent)) return "Chrome";
  if (/FxiOS\//i.test(userAgent)) return "Firefox";
  if (/Safari\//i.test(userAgent) && /Version\//i.test(userAgent)) return "Safari";
  return "Browser";
}

function deviceName(userAgent: string) {
  if (/iPad/i.test(userAgent)) return "iPad";
  if (/iPhone|iPod/i.test(userAgent)) return "iPhone";
  if (/Android/i.test(userAgent)) return /Mobile/i.test(userAgent) ? "Android phone" : "Android device";
  if (/Windows/i.test(userAgent)) return "Windows";
  if (/Macintosh|Mac OS X/i.test(userAgent)) return "Mac";
  if (/CrOS/i.test(userAgent)) return "Chromebook";
  if (/Linux/i.test(userAgent)) return "Linux";
  return "device";
}

export function describeSession(session: SessionInfo) {
  const userAgent = session.user_agent?.trim() || "";
  if (userAgent) {
    return `${browserName(userAgent)} on ${deviceName(userAgent)}`;
  }
  const deviceId = session.device_id?.trim();
  if (deviceId && !/^web[-_:]?/i.test(deviceId)) return deviceId;
  return "Unknown device";
}

export function describeNotificationDevice(platform: string) {
  const normalized = platform.trim().toLowerCase();
  if (normalized === "android") return "Android device";
  if (normalized === "ios") return "iPhone or iPad";
  return "Web browser";
}

export function formatRelativeActivity(value?: string | null) {
  if (!value) return "Activity unavailable";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Activity unavailable";
  const seconds = Math.round((date.getTime() - Date.now()) / 1000);
  const absolute = Math.abs(seconds);
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  if (absolute < 60) return formatter.format(seconds, "second");
  const minutes = Math.round(seconds / 60);
  if (Math.abs(minutes) < 60) return formatter.format(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) return formatter.format(hours, "hour");
  const days = Math.round(hours / 24);
  if (Math.abs(days) < 30) return formatter.format(days, "day");
  return date.toLocaleDateString();
}
