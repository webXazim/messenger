import http from 'k6/http';
import ws from 'k6/ws';
import exec from 'k6/execution';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';
import {
  authHeaders,
  baseUrl,
  origin,
  parseJson,
  requireConfiguration,
  runId,
  userForVu,
  users,
  wsUrl,
} from './lib/config.js';

requireConfiguration();
const targetVus = Number(__ENV.TARGET_VUS || Math.min(100, users.length));
const iterationsPerVu = Number(__ENV.ITERATIONS_PER_VU || 3);
const holdMs = Number(__ENV.RECONNECT_HOLD_MS || 3000);
const reconnectFailures = new Rate('reconnect_failures');
const reconnectLatency = new Trend('reconnect_latency', true);

export const options = {
  scenarios: {
    reconnect_storm: {
      executor: 'per-vu-iterations',
      vus: targetVus,
      iterations: iterationsPerVu,
      maxDuration: __ENV.MAX_DURATION || '10m',
      gracefulStop: '15s',
      exec: 'reconnectSocket',
      tags: { test: 'reconnect-storm', run_id: runId },
    },
  },
  thresholds: {
    checks: ['rate>0.99'],
    reconnect_failures: ['rate<0.01'],
    reconnect_latency: ['p(95)<2000', 'p(99)<4000'],
    ws_connecting: ['p(95)<1800', 'p(99)<4000'],
  },
};

function credentials(user) {
  const ticketResponse = http.post(
    `${baseUrl}/api/v1/realtime/tickets/`,
    JSON.stringify({ device_id: user.device_id, device_type: 'loadtest' }),
    { headers: authHeaders(user), tags: { endpoint: 'realtime_ticket' }, timeout: '10s' },
  );
  const ticket = parseJson(ticketResponse)?.ticket;
  const grantResponse = http.post(
    `${baseUrl}/api/v1/realtime/grants/`,
    JSON.stringify({ audiences: [{ kind: 'conversation', id: user.conversation_id }] }),
    { headers: authHeaders(user), tags: { endpoint: 'realtime_grant' }, timeout: '10s' },
  );
  const grant = parseJson(grantResponse)?.grants?.[0]?.grant;
  if (ticketResponse.status !== 201 || grantResponse.status !== 200 || !ticket || !grant) return null;
  return { ticket, grant };
}

export function reconnectSocket() {
  const user = userForVu(exec.vu.idInTest);
  const auth = credentials(user);
  if (!auth) {
    reconnectFailures.add(true);
    return;
  }
  const started = Date.now();
  let subscribed = false;
  const result = ws.connect(
    `${wsUrl}?ticket=${encodeURIComponent(auth.ticket)}`,
    { headers: { Origin: origin, 'User-Agent': 'k6-reconnect-test' } },
    (socket) => {
      socket.on('open', () => {
        socket.send(JSON.stringify({
          v: 1,
          event: 'audience.subscribe',
          request_id: `reconnect-${exec.vu.idInTest}-${Date.now()}`,
          data: {
            audience: { kind: 'conversation', id: user.conversation_id },
            grant: auth.grant,
          },
        }));
        socket.setTimeout(() => socket.close(), holdMs);
      });
      socket.on('message', (raw) => {
        try {
          const message = JSON.parse(raw);
          if (message.event === 'audience.subscribed') {
            subscribed = true;
            reconnectLatency.add(Date.now() - started);
          }
        } catch (_) {}
      });
      socket.on('error', () => reconnectFailures.add(true));
    },
  );
  const ok = check(result, { 'reconnect upgraded': (response) => response && response.status === 101 });
  reconnectFailures.add(!ok || !subscribed);
  sleep(Math.random() * 0.8 + 0.2);
}
