export type PresenceDeviceType = "desktop" | "mobile" | "tablet";
export type PresenceActivityStatus = "active" | "idle";

export const PRESENCE_IDLE_AFTER_MS = 5 * 60_000;

export function detectPresenceDeviceType(): PresenceDeviceType {
  const navigatorWithHints = navigator as Navigator & { userAgentData?: { mobile?: boolean } };
  const userAgent = navigator.userAgent.toLowerCase();
  const tablet = /ipad|tablet|kindle|silk|playbook/.test(userAgent)
    || (/macintosh/.test(userAgent) && navigator.maxTouchPoints > 1)
    || (/android/.test(userAgent) && !/mobile/.test(userAgent));
  if (tablet) return "tablet";
  if (navigatorWithHints.userAgentData?.mobile || /iphone|ipod|android.*mobile|windows phone|mobile/.test(userAgent)) {
    return "mobile";
  }
  return "desktop";
}
