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

const socketVus = Number(__ENV.MIXED_SOCKET_VUS || Math.min(250, users.length));
const warmupDuration = __ENV.MIXED_WARMUP_DURATION || '60s';
const holdDuration = __ENV.MIXED_HOLD_DURATION || '300s';
const rampDownDuration = __ENV.MIXED_RAMP_DOWN_DURATION || '30s';
const socketLifetimeMs = Number(__ENV.MIXED_SOCKET_LIFETIME_MS || 390000);
const readRate = Number(__ENV.MIXED_READ_RATE || 20);
const writeRate = Number(__ENV.MIXED_WRITE_RATE || 5);
const readPreallocated = Number(__ENV.MIXED_READ_PREALLOCATED_VUS || Math.min(40, users.length));
const readMax = Number(__ENV.MIXED_READ_MAX_VUS || Math.min(120, users.length));
const writePreallocated = Number(__ENV.MIXED_WRITE_PREALLOCATED_VUS || Math.min(25, users.length));
const writeMax = Number(__ENV.MIXED_WRITE_MAX_VUS || Math.min(80, users.length));

const wsFailures = new Rate('mixed_ws_failures');
const subscriptionRate = new Rate('mixed_subscriptions');
const readFailures = new Rate('mixed_read_failures');
const writeFailures = new Rate('mixed_write_failures');
const controlLatency = new Trend('mixed_control_latency', true);
const conversationReadLatency = new Trend('mixed_conversation_read_latency', true);
const messageReadLatency = new Trend('mixed_message_read_latency', true);
const messageWriteLatency = new Trend('mixed_message_write_latency', true);
const eventsReceived = new Counter('mixed_events_received');
const appPongs = new Counter('mixed_app_pongs');

export const options = {
  scenarios: {
    mixed_realtime_clients: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: warmupDuration, target: socketVus },
        { duration: holdDuration, target: socketVus },
        { duration: rampDownDuration, target: 0 },
      ],
      gracefulRampDown: '20s',
      gracefulStop: '20s',
      exec: 'mixedSocket',
      tags: { test: 'mixed-production', component: 'realtime', run_id: runId },
    },
    mixed_reads: {
      executor: 'constant-arrival-rate',
      startTime: warmupDuration,
      rate: readRate,
      timeUnit: '1s',
      duration: holdDuration,
      preAllocatedVUs: readPreallocated,
      maxVUs: readMax,
      exec: 'mixedRead',
      tags: { test: 'mixed-production', component: 'read', run_id: runId },
    },
    mixed_writes: {
      executor: 'constant-arrival-rate',
      startTime: warmupDuration,
      rate: writeRate,
      timeUnit: '1s',
      duration: holdDuration,
      preAllocatedVUs: writePreallocated,
      maxVUs: writeMax,
      exec: 'mixedWrite',
      tags: { test: 'mixed-production', component: 'write', run_id: runId },
    },
  },
  thresholds: {
    checks: ['rate>0.99'],
    mixed_ws_failures: ['rate<0.01'],
    mixed_subscriptions: ['rate>0.99'],
    mixed_read_failures: ['rate<0.01'],
    mixed_write_failures: ['rate<0.01'],
    mixed_control_latency: ['p(95)<350', 'p(99)<1000'],
    mixed_conversation_read_latency: ['p(95)<500', 'p(99)<1200'],
    mixed_message_read_latency: ['p(95)<500', 'p(99)<1200'],
    mixed_message_write_latency: ['p(95)<500', 'p(99)<1200'],
    'http_req_failed{endpoint:mixed_conversations}': ['rate<0.01'],
    'http_req_failed{endpoint:mixed_messages}': ['rate<0.01'],
    'http_req_failed{endpoint:mixed_message_send}': ['rate<0.01'],
    'http_req_duration{endpoint:mixed_conversations}': ['p(95)<500', 'p(99)<1200'],
    'http_req_duration{endpoint:mixed_messages}': ['p(95)<500', 'p(99)<1200'],
    'http_req_duration{endpoint:mixed_message_send}': ['p(95)<500', 'p(99)<1200'],
    dropped_iterations: ['count<1'],
    mixed_events_received: ['count>0'],
  },
  discardResponseBodies: false,
};

function obtainCredentials(user) {
  const ticketResponse = http.post(
    `${baseUrl}/api/v1/realtime/tickets/`,
    JSON.stringify({ device_id: user.device_id, device_type: user.device_type || 'loadtest' }),
    { headers: authHeaders(user), tags: { endpoint: 'mixed_realtime_ticket' }, timeout: '10s' },
  );
  const ticket = parseJson(ticketResponse)?.ticket;
  const grantResponse = http.post(
    `${baseUrl}/api/v1/realtime/grants/`,
    JSON.stringify({ audiences: [{ kind: 'conversation', id: user.conversation_id }] }),
    { headers: authHeaders(user), tags: { endpoint: 'mixed_realtime_grant' }, timeout: '10s' },
  );
  const grant = parseJson(grantResponse)?.grants?.[0]?.grant;
  const valid = check(ticketResponse, {
    'mixed ticket created': (response) => response.status === 201 && Boolean(ticket),
  }) && check(grantResponse, {
    'mixed grant created': (response) => response.status === 200 && Boolean(grant),
  });
  return valid ? { ticket, grant } : null;
}

export function mixedSocket() {
  const user = userForVu(exec.vu.idInTest);
  const credentials = obtainCredentials(user);
  if (!credentials) {
    wsFailures.add(true);
    subscriptionRate.add(false);
    return;
  }

  const pending = new Map();
  let subscribed = false;
  let sequence = 0;
  const result = ws.connect(
    `${wsUrl}?ticket=${encodeURIComponent(credentials.ticket)}`,
    { headers: { Origin: origin, 'User-Agent': 'k6-crescentsphere-mixed-load' } },
    (socket) => {
      socket.on('open', () => {
        wsFailures.add(false);
        const requestId = `mixed-sub-${exec.vu.idInTest}-${Date.now()}`;
        pending.set(requestId, Date.now());
        socket.send(JSON.stringify({
          v: 1,
          event: 'audience.subscribe',
          request_id: requestId,
          data: {
            audience: { kind: 'conversation', id: user.conversation_id },
            grant: credentials.grant,
          },
        }));

        socket.setInterval(() => {
          const requestId = `mixed-ping-${exec.vu.idInTest}-${sequence++}-${Date.now()}`;
          pending.set(requestId, Date.now());
          socket.send(JSON.stringify({ v: 1, event: 'ping', request_id: requestId, data: {} }));
        }, 10000);

        socket.setInterval(() => {
          socket.send(JSON.stringify({
            v: 1,
            event: sequence % 2 === 0 ? 'typing.start' : 'typing.stop',
            request_id: `mixed-typing-${exec.vu.idInTest}-${Date.now()}`,
            data: { conversation_id: user.conversation_id },
          }));
        }, 19000);

        socket.setTimeout(() => socket.close(), socketLifetimeMs);
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
          subscriptionRate.add(subscribed);
        }
        if (message.event === 'pong') appPongs.add(1);
        if (message.type === 'chat.event' || message.event === 'message.created') {
          eventsReceived.add(1);
        }
      });
      socket.on('error', () => wsFailures.add(true));
    },
  );

  const upgraded = check(result, { 'mixed websocket upgraded': (response) => response && response.status === 101 });
  if (!upgraded) wsFailures.add(true);
  if (!subscribed) subscriptionRate.add(false);
}

export function mixedRead() {
  const user = userForVu(exec.vu.idInTest);

  let started = Date.now();
  const conversations = http.get(`${baseUrl}/api/v1/chat-fast/conversations/`, {
    headers: authHeaders(user),
    tags: { endpoint: 'mixed_conversations' },
    timeout: '10s',
  });
  conversationReadLatency.add(Date.now() - started);
  const conversationsOk = check(conversations, {
    'mixed conversation list returned': (response) => response.status === 200,
  });

  started = Date.now();
  const messages = http.get(
    `${baseUrl}/api/v1/chat-fast/conversations/${user.conversation_id}/messages/?page_size=30`,
    {
      headers: authHeaders(user),
      tags: { endpoint: 'mixed_messages' },
      timeout: '10s',
    },
  );
  messageReadLatency.add(Date.now() - started);
  const messagesOk = check(messages, {
    'mixed message page returned': (response) => response.status === 200,
  });

  readFailures.add(!(conversationsOk && messagesOk));
}

export function mixedWrite() {
  const user = userForVu(exec.vu.idInTest);
  const clientTempId = `mixed-${runId}-${exec.scenario.iterationInTest}-${exec.vu.idInTest}-${Date.now()}`;
  const started = Date.now();
  const response = http.post(
    `${baseUrl}/api/v1/chat-fast/conversations/${user.conversation_id}/messages/`,
    JSON.stringify({
      type: 'text',
      text: `Mixed load ${runId} ${clientTempId}`,
      client_temp_id: clientTempId,
    }),
    {
      headers: authHeaders(user, { 'Idempotency-Key': clientTempId }),
      tags: { endpoint: 'mixed_message_send' },
      timeout: '10s',
    },
  );
  messageWriteLatency.add(Date.now() - started);
  const accepted = check(response, {
    'mixed message accepted': (result) => result.status === 201 || result.status === 200,
  });
  writeFailures.add(!accepted);
}
