import http from 'k6/http';
import ws from 'k6/ws';
import exec from 'k6/execution';
import { check } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';
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
const rampDuration = __ENV.RAMP_DURATION || '60s';
const holdDuration = __ENV.HOLD_DURATION || '180s';
const rampDownDuration = __ENV.RAMP_DOWN_DURATION || '30s';
const socketLifetimeMs = Number(__ENV.SOCKET_LIFETIME_MS || 240000);
const pingIntervalMs = Number(__ENV.PING_INTERVAL_MS || 10000);
const presenceIntervalMs = Number(__ENV.PRESENCE_INTERVAL_MS || 25000);
const typingIntervalMs = Number(__ENV.TYPING_INTERVAL_MS || 17000);

const wsFailures = new Rate('realtime_ws_failures');
const controlLatency = new Trend('realtime_control_latency', true);
const subscriptions = new Rate('realtime_subscriptions');
const appPongs = new Counter('realtime_app_pongs');
const receivedEvents = new Counter('realtime_events_received');

export const options = {
  scenarios: {
    realtime_capacity: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: rampDuration, target: targetVus },
        { duration: holdDuration, target: targetVus },
        { duration: rampDownDuration, target: 0 },
      ],
      gracefulRampDown: '20s',
      gracefulStop: '20s',
      exec: 'capacitySocket',
      tags: { test: 'realtime-capacity', run_id: runId },
    },
  },
  thresholds: {
    checks: ['rate>0.99'],
    realtime_ws_failures: ['rate<0.01'],
    realtime_subscriptions: ['rate>0.99'],
    realtime_control_latency: ['p(95)<300', 'p(99)<1000'],
    ws_connecting: ['p(95)<1500', 'p(99)<3000'],
  },
  discardResponseBodies: false,
};

function obtainCredentials(user) {
  const ticketResponse = http.post(
    `${baseUrl}/api/v1/realtime/tickets/`,
    JSON.stringify({ device_id: user.device_id, device_type: user.device_type || 'loadtest' }),
    { headers: authHeaders(user), tags: { endpoint: 'realtime_ticket' }, timeout: '10s' },
  );
  const ticketPayload = parseJson(ticketResponse);
  const ticketOk = check(ticketResponse, {
    'ticket status is 201': (response) => response.status === 201,
    'ticket exists': () => Boolean(ticketPayload && ticketPayload.ticket),
  });
  if (!ticketOk || !ticketPayload?.ticket) return null;

  const audience = { kind: 'conversation', id: user.conversation_id };
  const grantResponse = http.post(
    `${baseUrl}/api/v1/realtime/grants/`,
    JSON.stringify({ audiences: [audience] }),
    { headers: authHeaders(user), tags: { endpoint: 'realtime_grant' }, timeout: '10s' },
  );
  const grantPayload = parseJson(grantResponse);
  const grant = grantPayload?.grants?.[0]?.grant;
  const grantOk = check(grantResponse, {
    'grant status is 200': (response) => response.status === 200,
    'grant exists': () => Boolean(grant),
  });
  if (!grantOk || !grant) return null;
  return { ticket: ticketPayload.ticket, grant, audience };
}

export function capacitySocket() {
  const user = userForVu(exec.vu.idInTest);
  const credentials = obtainCredentials(user);
  if (!credentials) {
    wsFailures.add(true);
    return;
  }

  const pending = new Map();
  let subscribed = false;
  let sequence = 0;
  const response = ws.connect(
    `${wsUrl}?ticket=${encodeURIComponent(credentials.ticket)}`,
    { headers: { Origin: origin, 'User-Agent': 'k6-crescentsphere-load-test' }, tags: { run_id: runId } },
    (socket) => {
      socket.on('open', () => {
        wsFailures.add(false);
        const requestId = `sub-${exec.vu.idInTest}-${Date.now()}`;
        pending.set(requestId, Date.now());
        socket.send(JSON.stringify({
          v: 1,
          event: 'audience.subscribe',
          request_id: requestId,
          data: { audience: credentials.audience, grant: credentials.grant },
        }));

        socket.setInterval(() => {
          const pingId = `ping-${exec.vu.idInTest}-${sequence++}-${Date.now()}`;
          pending.set(pingId, Date.now());
          socket.send(JSON.stringify({ v: 1, event: 'ping', request_id: pingId, data: {} }));
        }, pingIntervalMs);

        socket.setInterval(() => {
          socket.send(JSON.stringify({
            v: 1,
            event: 'presence.ping',
            request_id: `presence-${exec.vu.idInTest}-${Date.now()}`,
            data: { device_type: 'loadtest', presence_status: 'active' },
          }));
        }, presenceIntervalMs);

        socket.setInterval(() => {
          const started = sequence % 2 === 0;
          socket.send(JSON.stringify({
            v: 1,
            event: started ? 'typing.start' : 'typing.stop',
            request_id: `typing-${exec.vu.idInTest}-${Date.now()}`,
            data: { conversation_id: user.conversation_id },
          }));
        }, typingIntervalMs);

        socket.setTimeout(() => {
          socket.close();
        }, socketLifetimeMs);
      });

      socket.on('message', (raw) => {
        let message;
        try { message = JSON.parse(raw); } catch (_) { return; }
        if (message.request_id && pending.has(message.request_id)) {
          controlLatency.add(Date.now() - pending.get(message.request_id));
          pending.delete(message.request_id);
        }
        if (message.event === 'audience.subscribed') {
          subscribed = message.data?.subscribed === true;
          subscriptions.add(subscribed);
        }
        if (message.event === 'pong') appPongs.add(1);
        if (message.type === 'chat.event') receivedEvents.add(1);
      });

      socket.on('error', () => wsFailures.add(true));
      socket.on('close', () => {});
    },
  );

  check(response, { 'websocket upgraded': (result) => result && result.status === 101 });
  if (!subscribed) subscriptions.add(false);
}
