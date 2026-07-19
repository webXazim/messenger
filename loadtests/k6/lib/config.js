import { SharedArray } from 'k6/data';

const dataPath = __ENV.LOADTEST_DATA || '/data/users.json';
const document = JSON.parse(open(dataPath));

export const users = new SharedArray('load-test-users', () => document.users || []);
export const runId = String(document.run_id || 'unknown');
export const baseUrl = String(__ENV.LOADTEST_BASE_URL || '').replace(/\/$/, '');
export const origin = String(__ENV.LOADTEST_ORIGIN || baseUrl).replace(/\/$/, '');
export const wsUrl = String(
  __ENV.LOADTEST_WS_URL || baseUrl.replace(/^http:/, 'ws:').replace(/^https:/, 'wss:') + '/ws',
);

export function requireConfiguration() {
  if (!baseUrl || !origin || !wsUrl) {
    throw new Error('LOADTEST_BASE_URL, LOADTEST_ORIGIN, and LOADTEST_WS_URL must be configured.');
  }
  if (!users.length) {
    throw new Error(`No load-test users were found in ${dataPath}.`);
  }
}

export function userForVu(vuId) {
  return users[(Number(vuId) - 1) % users.length];
}

export function authHeaders(user, extra = {}) {
  return {
    Authorization: `Bearer ${user.access_token}`,
    Origin: origin,
    'Content-Type': 'application/json',
    'User-Agent': 'k6-crescentsphere-load-test',
    ...extra,
  };
}

export function unwrap(payload) {
  if (payload && typeof payload === 'object' && payload.data && typeof payload.data === 'object') {
    return payload.data;
  }
  return payload;
}

export function parseJson(response) {
  try {
    return unwrap(response.json());
  } catch (_) {
    return null;
  }
}
