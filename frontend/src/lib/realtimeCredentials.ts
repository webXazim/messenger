import { http } from "./http";
import { unwrapData } from "./apiResponse";

export type RealtimeAudienceKind = "conversation" | "user" | "support_website" | "support_user";

export type RealtimeAudience = {
  kind: RealtimeAudienceKind;
  id: string;
};

type TicketResponse = {
  ticket: string;
  expires_in: number;
  expires_at: number;
  protocol_version: number;
};

type GrantResponse = {
  grants: Array<{
    audience: RealtimeAudience;
    grant: string;
    expires_in: number;
    expires_at: number;
  }>;
  protocol_version: number;
};


type CallGrantResponse = {
  grant: string;
  expires_in: number;
  expires_at: number;
  participant_ids: string[];
  protocol_version: number;
};

const callGrantCache = new Map<string, CallGrantResponse>();
const callGrantRequests = new Map<string, Promise<CallGrantResponse>>();

export async function requestRealtimeTicket(
  accessToken: string,
  deviceId: string,
  deviceType: string,
): Promise<TicketResponse> {
  const response = await http.post(
    "/realtime/tickets/",
    { device_id: deviceId, device_type: deviceType },
    { headers: { Authorization: `Bearer ${accessToken}` } },
  );
  return unwrapData<TicketResponse>(response.data);
}

export async function requestRealtimeGrants(
  accessToken: string,
  audiences: RealtimeAudience[],
): Promise<Map<string, string>> {
  if (!audiences.length) return new Map();
  const response = await http.post(
    "/realtime/grants/",
    { audiences },
    { headers: { Authorization: `Bearer ${accessToken}` } },
  );
  const payload = unwrapData<GrantResponse>(response.data);
  return new Map(
    payload.grants.map((item) => [`${item.audience.kind}:${item.audience.id}`, item.grant]),
  );
}

export function realtimeAudienceKey(audience: RealtimeAudience) {
  return `${audience.kind}:${audience.id}`;
}


export async function requestRealtimeCallGrant(callId: string): Promise<CallGrantResponse> {
  const normalized = String(callId || "").trim();
  if (!normalized) throw new Error("A call ID is required for realtime signaling.");
  const cached = callGrantCache.get(normalized);
  if (cached && cached.expires_at * 1000 > Date.now() + 15_000) return cached;
  const pending = callGrantRequests.get(normalized);
  if (pending) return pending;
  const request = http
    .post("/realtime/call-grants/", { call_id: normalized })
    .then((response) => unwrapData<CallGrantResponse>(response.data))
    .then((grant) => {
      callGrantCache.set(normalized, grant);
      return grant;
    })
    .finally(() => callGrantRequests.delete(normalized));
  callGrantRequests.set(normalized, request);
  return request;
}

export function clearRealtimeCallGrant(callId: string) {
  callGrantCache.delete(String(callId || "").trim());
}
