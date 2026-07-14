import { http } from "../lib/http";
import { API_BASE_URL, AUTH_API_BASE_URL } from "../lib/config";
import { unwrapCursorPage, unwrapData } from "../lib/apiResponse";
import { collectCursorPages, type CursorPage } from "../lib/pagination";
import { resolveMediaUrl } from "../lib/mediaUrl";
import type {
  CurrentUser,
  FriendRequest,
  LoginPayload,
  RegisterPayload,
  SessionInfo,
  TokenPair,
  UserSearchResult,
} from "../types/auth";

type UnknownRecord = Record<string, unknown>;

function centralPath(path: string) {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  if (AUTH_API_BASE_URL === API_BASE_URL) return normalizedPath;
  return `${AUTH_API_BASE_URL}${normalizedPath}`;
}

function asRecord(value: unknown): UnknownRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as UnknownRecord) : {};
}

function firstString(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value;
  }
  return "";
}

function firstNumberOrNull(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) return Number(value);
  }
  return null;
}

function firstBoolean(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "boolean") return value;
  }
  return undefined;
}

function normalizeCurrentUser(user: unknown): CurrentUser {
  const item = asRecord(user);
  const profile = asRecord(item.profile);
  const displayName = firstString(item.display_name, profile.display_name);
  const bio = firstString(item.bio, profile.bio);
  const normalized: CurrentUser = {
    id: String(item.id ?? ""),
    username: firstString(item.username, item.email),
    email: firstString(item.email) || undefined,
    email_verified: firstBoolean(item.email_verified),
    email_verified_at: firstString(item.email_verified_at) || null,
    first_name: firstString(item.first_name) || undefined,
    last_name: firstString(item.last_name) || undefined,
    display_name: displayName || undefined,
    full_name: firstString(item.full_name, displayName, item.username, item.email) || undefined,
    is_staff: firstBoolean(item.is_staff),
    is_superuser: firstBoolean(item.is_superuser),
    last_seen_at: firstString(item.last_seen_at) || null,
    profile: {
      display_name: displayName || undefined,
      bio: bio || undefined,
      status_message: firstString(item.status_message, profile.status_message) || undefined,
      avatar: resolveMediaUrl(firstString(item.avatar, item.avatar_url, profile.avatar)) || null,
      is_discoverable: firstBoolean(item.is_discoverable, profile.is_discoverable),
      show_online_status: firstBoolean(item.show_online_status, profile.show_online_status),
      nearby_discovery_enabled: firstBoolean(item.nearby_discovery_enabled, profile.nearby_discovery_enabled),
      latitude: firstNumberOrNull(item.latitude, profile.latitude),
      longitude: firstNumberOrNull(item.longitude, profile.longitude),
      location_updated_at: firstString(item.location_updated_at, profile.location_updated_at) || null,
    },
    social_accounts: Array.isArray(item.social_accounts) ? item.social_accounts as CurrentUser["social_accounts"] : [],
  };
  return normalized;
}

function normalizeUser(user: unknown): UserSearchResult {
  const item = asRecord(user);
  const profile = asRecord(item.profile);
  const first_name = firstString(item.first_name);
  const last_name = firstString(item.last_name);
  const full_name = firstString(
    item.full_name,
    profile.full_name,
    [first_name, last_name].filter(Boolean).join(" "),
  );
  const requestStatus = firstString(item.request_status, item.friendship_status, item.status)
    .toLowerCase()
    .replace("canceled", "cancelled");
  const normalizedStatus = requestStatus === "none" ? null : requestStatus || null;
  const isFriendStatus = normalizedStatus === "accepted" || normalizedStatus === "friends";

  return {
    id: String(item.id ?? ""),
    username: firstString(item.username),
    first_name: first_name || undefined,
    last_name: last_name || undefined,
    full_name: full_name || undefined,
    display_name: firstString(item.display_name, profile.display_name) || undefined,
    avatar: resolveMediaUrl(firstString(item.avatar, profile.avatar)) || null,
    bio: firstString(item.bio, profile.bio) || undefined,
    status_message: firstString(item.status_message, profile.status_message) || undefined,
    is_current_user: firstBoolean(item.is_current_user),
    distance_km: firstNumberOrNull(item.distance_km, item.proximity_km),
    is_friend: Boolean(item.is_friend ?? isFriendStatus),
    request_status: normalizedStatus,
    is_online: firstBoolean(item.is_online, profile.is_online),
    last_seen_at: firstString(item.last_seen_at, profile.last_seen_at) || null,
    presence_label: firstString(item.presence_label, profile.presence_label) || undefined,
    presence_visibility: firstString(item.presence_visibility, item.visibility, profile.presence_visibility) === "hidden" ? "hidden" : "public",
  };
}

function normalizeFriendRequest(value: unknown): FriendRequest {
  const item = asRecord(value);
  const normalizedStatus = firstString(item.status, item.request_status, item.friendship_status)
    .toLowerCase()
    .replace("canceled", "cancelled");
  return {
    id: String(item.id ?? ""),
    status: (normalizedStatus || "pending") as FriendRequest["status"],
    message: firstString(item.message) || undefined,
    created_at: firstString(item.created_at) || undefined,
    responded_at: firstString(item.responded_at) || null,
    from_user: normalizeUser(item.from_user ?? item.sender ?? item.requester),
    to_user: normalizeUser(item.to_user ?? item.receiver ?? item.target_user),
  };
}

function normalizeSession(value: unknown): SessionInfo {
  const item = asRecord(value);
  return {
    id: String(item.id ?? ""),
    device_id: firstString(item.device_id) || null,
    user_agent: firstString(item.user_agent) || null,
    ip_address: firstString(item.ip_address) || null,
    last_seen_at: firstString(item.last_seen_at) || null,
    expires_at: firstString(item.expires_at) || null,
    revoked_at: firstString(item.revoked_at) || null,
    is_current: firstBoolean(item.is_current),
  };
}

type PaginatedAuthRequestOptions<T> = {
  params?: Record<string, unknown>;
  signal?: AbortSignal;
  getKey?: (item: T) => string;
  maxPages?: number;
};

async function collectAuthPages<T>(
  initialPath: string,
  normalize: (value: unknown) => T,
  options: PaginatedAuthRequestOptions<T> = {},
): Promise<T[]> {
  let firstPage = true;
  return collectCursorPages<T>(
    initialPath,
    async (url, signal): Promise<CursorPage<T>> => {
      const response = await http.get(url, {
        signal,
        params: firstPage ? options.params : undefined,
      });
      firstPage = false;
      const page = unwrapCursorPage<unknown>(response.data);
      return {
        results: page.results.map(normalize),
        next: page.next,
        previous: page.previous,
      };
    },
    {
      signal: options.signal,
      getKey: options.getKey,
      maxPages: options.maxPages,
      baseUrl: AUTH_API_BASE_URL,
    },
  );
}


export const authApi = {
  async logout(payload: { refresh?: string | null; sessionId?: string | null; deviceId?: string | null; accessToken?: string | null }) {
    // The bundled backend uses explicit session revocation. Local token cleanup is
    // still immediate in AuthContext, while this call invalidates the server-side
    // refresh session when a session id is available.
    if (payload.sessionId) {
      await http.post(centralPath(`/accounts/sessions/${payload.sessionId}/revoke/`), undefined, {
        headers: payload.accessToken ? { Authorization: `Bearer ${payload.accessToken}` } : undefined,
      });
    }
  },
  async login(payload: LoginPayload) {
    const response = await http.post(centralPath("/auth/token/"), {
      username: payload.username,
      password: payload.password,
    });
    return unwrapData<TokenPair>(response.data);
  },
  async register(payload: RegisterPayload) {
    const response = await http.post(centralPath("/auth/register/"), {
      username: payload.username,
      email: payload.email,
      password: payload.password,
    });
    return unwrapData<Record<string, unknown> | null>(response.data);
  },
  async checkUsernameAvailability(username: string, signal?: AbortSignal) {
    const response = await http.get(centralPath("/auth/username-availability/"), {
      params: { username },
      signal,
    });
    return unwrapData<{ username: string; available: boolean; detail: string }>(response.data);
  },
  async me() {
    const response = await http.get(centralPath("/users/me/"));
    return normalizeCurrentUser(unwrapData<unknown>(response.data));
  },
  async requestPasswordReset(email: string) {
    const response = await http.post(centralPath("/accounts/password/reset/request/"), { email });
    return unwrapData<{ detail: string }>(response.data);
  },
  async confirmPasswordReset(token: string, newPassword: string) {
    const response = await http.post(centralPath("/accounts/password/reset/confirm/"), {
      token,
      new_password: newPassword,
    });
    return unwrapData<{ detail: string; revoked_sessions?: number }>(response.data);
  },
  async requestEmailVerification() {
    const response = await http.post(centralPath("/accounts/email/verify/request/"));
    return unwrapData<{ detail: string }>(response.data);
  },
  async resendRegistrationCode(email: string) {
    const response = await http.post(centralPath("/accounts/email/verify/request/"), { email });
    return unwrapData<{ detail: string }>(response.data);
  },
  async confirmRegistrationCode(email: string, code: string) {
    const response = await http.post(centralPath("/accounts/email/verify/confirm/"), { email, code });
    return unwrapData<{ detail: string }>(response.data);
  },
  async confirmEmailVerification(token: string) {
    const response = await http.post(centralPath("/accounts/email/verify/confirm/"), { token });
    return unwrapData<{ detail: string }>(response.data);
  },
  async changePassword(currentPassword: string, newPassword: string) {
    const response = await http.post(centralPath("/accounts/password/change/"), {
      current_password: currentPassword,
      new_password: newPassword,
    });
    return unwrapData<{ detail: string; revoked_sessions?: number }>(response.data);
  },
  async listSessions(signal?: AbortSignal) {
    const items = await collectAuthPages(centralPath("/accounts/sessions/"), normalizeSession, {
      signal,
      getKey: (item) => item.id,
    });
    return items.filter((item) => Boolean(item.id));
  },
  async revokeSession(sessionId: string) {
    const response = await http.post(centralPath(`/accounts/sessions/${sessionId}/revoke/`));
    return unwrapData<{ detail: string }>(response.data);
  },
  async revokeOtherSessions() {
    const response = await http.post(centralPath("/accounts/sessions/revoke-all/"));
    return unwrapData<{ detail: string; revoked_count?: number }>(response.data);
  },
  async exportAccount() {
    const response = await http.get(centralPath("/accounts/me/export/"));
    return unwrapData<Record<string, unknown>>(response.data);
  },
  async deleteAccount(password: string) {
    const response = await http.post(centralPath("/accounts/me/delete/"), { password });
    return response.status === 204 ? null : unwrapData<{ detail?: string } | null>(response.data);
  },
  async uploadAvatar(file: File) {
    const form = new FormData();
    form.append("avatar", file);
    const response = await http.put(centralPath("/users/me/avatar/"), form);
    return normalizeCurrentUser(unwrapData<unknown>(response.data));
  },
  async deleteAvatar() {
    const response = await http.delete(centralPath("/users/me/avatar/"));
    return normalizeCurrentUser(unwrapData<unknown>(response.data));
  },
  async updateMe(payload: Partial<CurrentUser> & { profile?: Record<string, unknown> | null }) {
    const update: Record<string, unknown> = {};
    if (Object.prototype.hasOwnProperty.call(payload, "first_name")) update.first_name = payload.first_name ?? "";
    if (Object.prototype.hasOwnProperty.call(payload, "last_name")) update.last_name = payload.last_name ?? "";
    if (Object.prototype.hasOwnProperty.call(payload, "email")) update.email = payload.email ?? "";

    const profileSource = payload.profile ?? (
      Object.prototype.hasOwnProperty.call(payload, "display_name")
        ? { display_name: payload.display_name ?? "" }
        : null
    );
    if (profileSource) {
      const profile: Record<string, unknown> = {};
      for (const key of [
        "display_name",
        "bio",
        "status_message",
        "is_discoverable",
        "show_online_status",
        "nearby_discovery_enabled",
        "latitude",
        "longitude",
      ]) {
        if (Object.prototype.hasOwnProperty.call(profileSource, key)) {
          profile[key] = profileSource[key];
        }
      }
      update.profile = profile;
    }

    const response = await http.patch(centralPath("/users/me/"), update);
    return normalizeCurrentUser(unwrapData<unknown>(response.data));
  },
  async searchUsers(query: string, signal?: AbortSignal) {
    const items = await collectCursorPages<UserSearchResult>(
      "/chat/users/search/",
      async (url, pageSignal): Promise<CursorPage<UserSearchResult>> => {
        const response = await http.get(url, {
          signal: pageSignal,
          params: url.endsWith("/search/") ? { q: query, paginated: 1 } : undefined,
        });
        const page = unwrapCursorPage<unknown>(response.data);
        return {
          results: page.results.map(normalizeUser),
          next: page.next,
          previous: page.previous,
        };
      },
      { signal, getKey: (item) => item.id, baseUrl: API_BASE_URL },
    );
    return items.filter((item) => Boolean(item.id));
  },
  async nearbyUsers(
    latitude: number,
    longitude: number,
    radiusKm = 25,
    limit = 20,
    shareLocation = true,
    signal?: AbortSignal,
  ) {
    const response = await http.get("/chat/users/nearby/", {
      params: { latitude, longitude, radius_km: radiusKm, limit, share_location: shareLocation },
      signal,
    });
    return unwrapCursorPage<unknown>(response.data).results.map(normalizeUser).filter((item) => Boolean(item.id));
  },
  async listFriendRequests(
    scope: "all" | "incoming" | "outgoing" | "friends" = "all",
    signal?: AbortSignal,
  ) {
    const items = await collectCursorPages<FriendRequest>(
      "/chat/friends/requests/",
      async (url, pageSignal): Promise<CursorPage<FriendRequest>> => {
        const response = await http.get(url, {
          signal: pageSignal,
          params: url.endsWith("/requests/") ? { scope } : undefined,
        });
        const page = unwrapCursorPage<unknown>(response.data);
        return {
          results: page.results.map(normalizeFriendRequest),
          next: page.next,
          previous: page.previous,
        };
      },
      { signal, getKey: (item) => item.id, baseUrl: API_BASE_URL },
    );
    return items.filter((item) => Boolean(item.id));
  },
  async createFriendRequest(userId: string, message?: string) {
    const numericUserId = Number(userId);
    const payload = {
      user_id: Number.isFinite(numericUserId) ? numericUserId : userId,
      ...(message ? { message } : {}),
    };
    const response = await http.post("/chat/friends/requests/", payload);
    return normalizeFriendRequest(unwrapData<unknown>(response.data));
  },
  async respondToFriendRequest(requestId: string, action: "accept" | "reject" | "cancel") {
    const response = await http.post(`/chat/friends/requests/${requestId}/respond/`, { action });
    return normalizeFriendRequest(unwrapData<unknown>(response.data));
  },
};
