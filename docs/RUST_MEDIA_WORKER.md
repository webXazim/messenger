# Rust media worker

The realtime Axum process must never run FFmpeg, decode media, or generate thumbnails. Those jobs can consume CPU and memory for seconds and would increase message and call latency.

## Boundary

Django and Celery remain responsible for upload authorization, executable-file policy, ClamAV scanning, private-storage configuration and explicit emergency recovery. After an upload is marked `clean`, Django creates one durable `MediaProcessingJob` in PostgreSQL.

The separate `crescentsphere-media-worker` process:

1. Claims a due job with `FOR UPDATE SKIP LOCKED` and a unique lease token.
2. Streams the approved object from local private storage or Cloudflare R2 to disk.
3. Uses `ffprobe` for trusted dimensions, rotation, duration, codecs and stream details.
4. Uses a bounded single-thread FFmpeg process for image/video thumbnails.
5. Decodes bounded mono PCM and calculates a compact waveform in Rust.
6. Uploads a deterministic private thumbnail.
7. Updates the pending upload and any attachment already created from it.
8. Marks the job complete only if the lease token still matches.

The default production configuration uses one worker slot and one FFmpeg thread for a 2 vCPU / 2 GB VPS. Increase concurrency only after load testing.

In `rust_shadow` mode the worker executes the same probe and generation workload, but records its observed result only in `MediaProcessingJob.result`; it does not replace Django-produced metadata or storage objects.

## Rollout

```bash
./scripts/generate-media-worker-lockfile.sh
./scripts/check-rust-media-worker.sh
./scripts/stack-profile.sh media-shadow
```

Shadow mode keeps Django enrichment enabled while the Rust output is compared. It temporarily duplicates processing and should be used only for validation.

Cut over:

```bash
./scripts/stack-profile.sh media
```

Rollback:

```bash
./scripts/stack-profile.sh media-rollback
```

The Django emergency fallback is disabled by default. Enable `MEDIA_WORKER_DJANGO_FALLBACK_ENABLED=True` only during an incident after the Rust worker has exhausted its configured attempts.

Additional decode guards:

```env
MEDIA_WORKER_MAX_FRAME_DIMENSION=32768
MEDIA_WORKER_MAX_FRAME_PIXELS=80000000
```

These bounds reject decompression-bomb-style images or abnormal video frames before FFmpeg thumbnail generation.
