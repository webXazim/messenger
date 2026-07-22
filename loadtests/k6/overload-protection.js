import http from 'k6/http';
import exec from 'k6/execution';
import { check } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';
import { authHeaders, baseUrl, parseJson, requireConfiguration, runId, userForVu, users } from './lib/config.js';

requireConfiguration();
const readRate = Number(__ENV.OVERLOAD_READ_RATE || 120);
const writeRate = Number(__ENV.OVERLOAD_WRITE_RATE || 40);
const duration = __ENV.OVERLOAD_DURATION || '120s';
const maxVus = Number(__ENV.OVERLOAD_MAX_VUS || Math.min(300, users.length));
const controlledShed = new Counter('overload_controlled_shed');
const unexpectedFailures = new Rate('overload_unexpected_failures');
const responseLatency = new Trend('overload_response_latency', true);

export const options = {
  scenarios: {
    overload_reads: {
      executor: 'constant-arrival-rate', rate: readRate, timeUnit: '1s', duration,
      preAllocatedVUs: Math.min(80, maxVus), maxVUs, exec: 'readPath',
      tags: { test: 'overload-protection', component: 'read', run_id: runId },
    },
    overload_writes: {
      executor: 'constant-arrival-rate', rate: writeRate, timeUnit: '1s', duration,
      preAllocatedVUs: Math.min(50, maxVus), maxVUs, exec: 'writePath',
      tags: { test: 'overload-protection', component: 'write', run_id: runId },
    },
  },
  thresholds: {
    checks: ['rate>0.99'],
    overload_unexpected_failures: ['rate<0.01'],
    overload_response_latency: ['p(99)<11000'],
    'http_req_duration{test:overload-protection}': ['p(99)<11000'],
  },
  discardResponseBodies: false,
};

function classify(response) {
  responseLatency.add(response.timings.duration);
  if (response.status === 503) {
    const payload = parseJson(response);
    const controlled = payload?.code === 'realtime_overloaded' && response.headers['Retry-After'] === '1';
    controlledShed.add(controlled ? 1 : 0);
    unexpectedFailures.add(!controlled);
    return controlled;
  }
  const accepted = response.status >= 200 && response.status < 300;
  unexpectedFailures.add(!accepted);
  return accepted;
}

export function readPath() {
  const user = userForVu(exec.vu.idInTest);
  const response = http.get(`${baseUrl}/api/v1/chat-fast/conversations/${user.conversation_id}/messages/?page_size=30`, {
    headers: authHeaders(user), tags: { endpoint: 'overload_read', test: 'overload-protection' }, timeout: '12s',
  });
  check(response, { 'read succeeded or was deliberately shed': classify });
}

export function writePath() {
  const user = userForVu(exec.vu.idInTest);
  const id = `overload-${runId}-${exec.scenario.iterationInTest}-${exec.vu.idInTest}-${Date.now()}`;
  const response = http.post(
    `${baseUrl}/api/v1/chat-fast/conversations/${user.conversation_id}/messages/`,
    JSON.stringify({ type: 'text', text: `Overload test ${id}`, client_temp_id: id }),
    { headers: authHeaders(user, { 'Idempotency-Key': id }), tags: { endpoint: 'overload_write', test: 'overload-protection' }, timeout: '12s' },
  );
  check(response, { 'write succeeded or was deliberately shed': classify });
}
