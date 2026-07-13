import { API_BASE_URL } from "./config";

export function resolveMediaUrl(value?: string | null) {
  const source = String(value || "").trim();
  if (!source) return undefined;
  if (/^(blob:|data:)/i.test(source)) return source;

  try {
    const currentOrigin = typeof window !== "undefined" ? window.location.origin : "http://localhost";
    const apiBase = new URL(API_BASE_URL, currentOrigin);
    const target = new URL(source, apiBase);

    if ((target.pathname.startsWith("/api/") || target.pathname.startsWith("/media/")) && target.hostname === apiBase.hostname) {
      target.protocol = apiBase.protocol;
      target.host = apiBase.host;
    }

    return target.toString();
  } catch {
    return source;
  }
}
