import { AUTH_API_BASE_URL } from "./config";
import { unwrapData } from "./apiResponse";
import {
  clearTokens,
  getAccessToken,
  getRefreshToken,
  setAccessToken,
  setRefreshToken,
  setSessionId,
} from "./tokenStore";

let refreshInFlight: Promise<string | null> | null = null;

export function jwtExpiresSoon(token: string, skewSeconds = 60) {
  try {
    const segment = token.split(".")[1] ?? "";
    const normalized = segment.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized.padEnd(
      normalized.length + ((4 - (normalized.length % 4)) % 4),
      "=",
    );
    const payload = JSON.parse(atob(padded)) as { exp?: number };
    return (
      typeof payload.exp !== "number"
      || payload.exp * 1000 <= Date.now() + skewSeconds * 1000
    );
  } catch {
    return true;
  }
}

async function refreshAccessToken(force: boolean) {
  const currentAccess = getAccessToken();
  if (!force && currentAccess && !jwtExpiresSoon(currentAccess)) {
    return currentAccess;
  }

  const submittedRefresh = getRefreshToken();
  if (!submittedRefresh) {
    if (force || !currentAccess) clearTokens();
    return force ? null : currentAccess;
  }

  try {
    const response = await fetch(`${AUTH_API_BASE_URL}/auth/token/refresh/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh: submittedRefresh }),
    });
    if (!response.ok) {
      // A newer refresh may already have rotated the token. Never let an older
      // failed request erase credentials created by that successful rotation.
      if (getRefreshToken() === submittedRefresh) clearTokens();
      return getAccessToken();
    }
    const data = unwrapData<{
      access?: string;
      refresh?: string;
      session_id?: string;
    }>(await response.json());
    if (!data.access) {
      if (getRefreshToken() === submittedRefresh) clearTokens();
      return getAccessToken();
    }
    setAccessToken(data.access);
    if (data.refresh) setRefreshToken(data.refresh);
    if (data.session_id) setSessionId(data.session_id);
    return data.access;
  } catch {
    // Network loss is not proof that the refresh credential is invalid. Keep
    // the session so online/focus recovery can retry it.
    return getAccessToken();
  }
}

export function refreshAccessTokenOnce(force = false) {
  if (!refreshInFlight) {
    refreshInFlight = refreshAccessToken(force).finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}
