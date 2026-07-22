# Axum call runtime

This phase moves the high-frequency call data plane from Django to Axum and SQLx. WebRTC audio and video remain peer-to-peer in browsers, with Cloudflare TURN used only when browser relay is required. Axum never receives or relays RTP media.

## Axum-owned endpoints

- Recent calls and call lifecycle: start, get, accept, decline, end
- HTTP signaling fallback
- Participant heartbeat
- Microphone, camera, hold, reconnect, screen-share, raised-hand, route and connection state
- Quality reports and recovery recommendations
- Active-speaker state
- Orchestration and diagnostics reads

## Django-owned endpoints

- Calling policy/configuration
- RSA call grant issuance
- Cloudflare TURN credential generation
- Scheduled stale-participant recovery and other background administration
- Admin and moderation policy

## Runtime behavior

Disposable runtime events use Core NATS and do not create transactional outbox rows. Heartbeats, media state, quality reports and speaker state update the call participant row through SQLx and fan out an ephemeral event. Call lifecycle commands still use the durable transactional outbox.

HTTP fallback signals are held in a bounded, TTL-limited Axum queue. Core NATS copies targeted signals to every Axum node so a later orchestration poll can retrieve them regardless of which node handles the request. Signal IDs are deduplicated per call and recipient.

Orchestration is computed from current participant state and is not repeatedly persisted into `CallSession.metadata`, reducing write amplification.

## Rollout

Expose the routes while the frontend remains on Django:

```bash
./scripts/stack-profile.sh calls-shadow
```

Run the contract check:

```bash
./scripts/check-axum-call-runtime.sh
```

Move only the call lifecycle and runtime APIs:

```bash
./scripts/stack-profile.sh calls
```

Expected selectors:

```env
REALTIME_EPHEMERAL_BACKEND=nats
CHAT_COMMAND_BACKEND=django
CHAT_CALL_RUNTIME_BACKEND=axum
VITE_CHAT_COMMAND_BACKEND=django
VITE_CHAT_CALL_RUNTIME_BACKEND=axum
```

`CHAT_COMMAND_BACKEND` remains independent. Message creation does not move when the call runtime is enabled.

## Rollback

Restore the environment snapshot reported by `stack-profile.sh`, or set:

```env
CHAT_CALL_RUNTIME_BACKEND=django
VITE_CHAT_CALL_RUNTIME_BACKEND=django
```

Then rebuild `frontend` and restart `realtime`.
