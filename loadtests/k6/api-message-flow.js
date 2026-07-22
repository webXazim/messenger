import http from 'k6/http';
import exec from 'k6/execution';
import { check } from 'k6';
import { Rate, Trend } from 'k6/metrics';
import {
  authHeaders,
  baseUrl,
  requireConfiguration,
  runId,
  userForVu,
  users,
} from './lib/config.js';

requireConfiguration();
const rate = Number(__ENV.MESSAGE_RATE || 10);
const duration = __ENV.MESSAGE_DURATION || '180s';
const preAllocatedVus = Number(__ENV.PREALLOCATED_VUS || Math.min(50, users.length));
const maxVus = Number(__ENV.MAX_VUS || Math.min(150, users.length));
const messageFailures = new Rate('message_failures');
const messageAck = new Trend('message_ack_duration', true);

export const options = {
  scenarios: {
    message_api: {
      executor: 'constant-arrival-rate',
      rate,
      timeUnit: '1s',
      duration,
      preAllocatedVUs,
      maxVUs,
      exec: 'sendMessage',
      tags: { test: 'api-message-flow', run_id: runId },
    },
  },
  thresholds: {
    checks: ['rate>0.99'],
    message_failures: ['rate<0.01'],
    'http_req_failed{endpoint:message_send}': ['rate<0.01'],
    'http_req_duration{endpoint:message_send}': ['p(95)<400', 'p(99)<1000'],
    message_ack_duration: ['p(95)<400', 'p(99)<1000'],
    dropped_iterations: ['count<1'],
  },
};

export function sendMessage() {
  const user = userForVu(exec.vu.idInTest);
  const clientTempId = `loadtest-${runId}-${exec.vu.idInTest}-${exec.scenario.iterationInTest}-${Date.now()}`;
  const started = Date.now();
  const response = http.post(
    `${baseUrl}/api/v1/chat-fast/conversations/${user.conversation_id}/messages/`,
    JSON.stringify({
      type: 'text',
      text: `Load test ${runId} ${clientTempId}`,
      client_temp_id: clientTempId,
    }),
    {
      headers: authHeaders(user, { 'Idempotency-Key': clientTempId }),
      tags: { endpoint: 'message_send' },
      timeout: '10s',
    },
  );
  messageAck.add(Date.now() - started);
  const ok = check(response, {
    'message accepted': (result) => result.status === 201 || result.status === 200,
  });
  messageFailures.add(!ok);
}
