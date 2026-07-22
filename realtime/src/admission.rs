use std::{
    array,
    sync::{
        atomic::{AtomicU64, AtomicUsize, Ordering},
        Arc,
    },
    time::Instant,
};

use axum::{
    extract::{Request, State},
    http::{header::RETRY_AFTER, HeaderValue, Method, StatusCode},
    middleware::Next,
    response::{IntoResponse, Response},
    Json,
};
use serde::Serialize;
use serde_json::json;
use tokio::{sync::Semaphore, time};

use crate::state::AppState;

const LATENCY_BUCKETS_MS: [u64; 8] = [10, 25, 50, 100, 250, 500, 1_000, 5_000];

pub struct HttpAdmission {
    read_slots: Arc<Semaphore>,
    write_slots: Arc<Semaphore>,
    max_read: usize,
    max_write: usize,
    read_requests: AtomicU64,
    write_requests: AtomicU64,
    rejected_read: AtomicU64,
    rejected_write: AtomicU64,
    timed_out: AtomicU64,
    server_errors: AtomicU64,
    in_flight: AtomicUsize,
    max_in_flight: AtomicUsize,
    total_duration_ms: AtomicU64,
    latency_buckets: [AtomicU64; LATENCY_BUCKETS_MS.len()],
}

#[derive(Debug, Serialize)]
pub struct HttpAdmissionSnapshot {
    pub read_limit: usize,
    pub write_limit: usize,
    pub available_read: usize,
    pub available_write: usize,
    pub in_flight: usize,
    pub max_in_flight: usize,
    pub read_requests: u64,
    pub write_requests: u64,
    pub rejected_read: u64,
    pub rejected_write: u64,
    pub timed_out: u64,
    pub server_errors: u64,
    pub total_duration_ms: u64,
    pub latency_buckets: Vec<(u64, u64)>,
}

impl HttpAdmission {
    pub fn new(max_read: usize, max_write: usize) -> Arc<Self> {
        Arc::new(Self {
            read_slots: Arc::new(Semaphore::new(max_read)),
            write_slots: Arc::new(Semaphore::new(max_write)),
            max_read,
            max_write,
            read_requests: AtomicU64::new(0),
            write_requests: AtomicU64::new(0),
            rejected_read: AtomicU64::new(0),
            rejected_write: AtomicU64::new(0),
            timed_out: AtomicU64::new(0),
            server_errors: AtomicU64::new(0),
            in_flight: AtomicUsize::new(0),
            max_in_flight: AtomicUsize::new(0),
            total_duration_ms: AtomicU64::new(0),
            latency_buckets: array::from_fn(|_| AtomicU64::new(0)),
        })
    }

    pub fn snapshot(&self) -> HttpAdmissionSnapshot {
        HttpAdmissionSnapshot {
            read_limit: self.max_read,
            write_limit: self.max_write,
            available_read: self.read_slots.available_permits(),
            available_write: self.write_slots.available_permits(),
            in_flight: self.in_flight.load(Ordering::Relaxed),
            max_in_flight: self.max_in_flight.load(Ordering::Relaxed),
            read_requests: self.read_requests.load(Ordering::Relaxed),
            write_requests: self.write_requests.load(Ordering::Relaxed),
            rejected_read: self.rejected_read.load(Ordering::Relaxed),
            rejected_write: self.rejected_write.load(Ordering::Relaxed),
            timed_out: self.timed_out.load(Ordering::Relaxed),
            server_errors: self.server_errors.load(Ordering::Relaxed),
            total_duration_ms: self.total_duration_ms.load(Ordering::Relaxed),
            latency_buckets: LATENCY_BUCKETS_MS
                .iter()
                .enumerate()
                .map(|(index, upper)| (*upper, self.latency_buckets[index].load(Ordering::Relaxed)))
                .collect(),
        }
    }

    fn begin(&self, is_read: bool) {
        if is_read {
            self.read_requests.fetch_add(1, Ordering::Relaxed);
        } else {
            self.write_requests.fetch_add(1, Ordering::Relaxed);
        }
        let current = self.in_flight.fetch_add(1, Ordering::Relaxed) + 1;
        let mut observed = self.max_in_flight.load(Ordering::Relaxed);
        while current > observed {
            match self.max_in_flight.compare_exchange_weak(
                observed,
                current,
                Ordering::Relaxed,
                Ordering::Relaxed,
            ) {
                Ok(_) => break,
                Err(next) => observed = next,
            }
        }
    }

    fn finish(&self, elapsed_ms: u64, status: StatusCode) {
        self.in_flight.fetch_sub(1, Ordering::Relaxed);
        self.total_duration_ms.fetch_add(elapsed_ms, Ordering::Relaxed);
        for (index, upper) in LATENCY_BUCKETS_MS.iter().enumerate() {
            if elapsed_ms <= *upper {
                self.latency_buckets[index].fetch_add(1, Ordering::Relaxed);
            }
        }
        if status.is_server_error() {
            self.server_errors.fetch_add(1, Ordering::Relaxed);
        }
    }
}

pub async fn guard(
    State(state): State<Arc<AppState>>,
    request: Request,
    next: Next,
) -> Response {
    let path = request.uri().path();
    if path == "/ws"
        || path.starts_with("/health/")
        || path.starts_with("/internal/")
    {
        return next.run(request).await;
    }

    let method = request.method();
    let is_read = method == Method::GET || method == Method::HEAD || method == Method::OPTIONS;
    let slots = if is_read {
        state.http_admission.read_slots.clone()
    } else {
        state.http_admission.write_slots.clone()
    };

    let permit = match slots.try_acquire_owned() {
        Ok(permit) => permit,
        Err(_) => {
            if is_read {
                state.http_admission.rejected_read.fetch_add(1, Ordering::Relaxed);
            } else {
                state.http_admission.rejected_write.fetch_add(1, Ordering::Relaxed);
            }
            let mut response = (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(json!({
                    "detail": "The realtime data plane is at capacity. Retry shortly.",
                    "code": "realtime_overloaded",
                })),
            )
                .into_response();
            response
                .headers_mut()
                .insert(RETRY_AFTER, HeaderValue::from_static("1"));
            return response;
        }
    };

    state.http_admission.begin(is_read);
    let started = Instant::now();
    let result = time::timeout(state.config.http_request_timeout, next.run(request)).await;
    drop(permit);

    match result {
        Ok(response) => {
            state.http_admission.finish(
                started.elapsed().as_millis().min(u128::from(u64::MAX)) as u64,
                response.status(),
            );
            response
        }
        Err(_) => {
            state.http_admission.timed_out.fetch_add(1, Ordering::Relaxed);
            state.http_admission.finish(
                started.elapsed().as_millis().min(u128::from(u64::MAX)) as u64,
                StatusCode::GATEWAY_TIMEOUT,
            );
            (
                StatusCode::GATEWAY_TIMEOUT,
                Json(json!({
                    "detail": "The realtime request exceeded its execution deadline.",
                    "code": "realtime_timeout",
                })),
            )
                .into_response()
        }
    }
}
