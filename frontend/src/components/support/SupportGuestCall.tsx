import { useCallback, useEffect, useRef, useState } from "react";
import { supportApi } from "../../api/support";
import { supportSocket, type SupportSocketEvent } from "../../lib/supportSocket";
import type { SupportCall, SupportCallSignal } from "../../types/support";

function errorMessage(error: unknown) {
  if (error && typeof error === "object" && "response" in error) {
    const data = (error as { response?: { data?: { detail?: string } } }).response?.data;
    if (data?.detail) return data.detail;
  }
  return error instanceof Error ? error.message : "The support call could not continue.";
}

function terminal(status: SupportCall["status"]) {
  return ["declined", "missed", "ended", "failed"].includes(status);
}

export function SupportGuestCall({ initialCall, onFinished }: { initialCall: SupportCall; onFinished: () => void }) {
  const [call, setCall] = useState(initialCall);
  const [error, setError] = useState("");
  const [muted, setMuted] = useState(false);
  const [cameraEnabled, setCameraEnabled] = useState(initialCall.call_type === "video");
  const [remoteConnected, setRemoteConnected] = useState(false);
  const localVideoRef = useRef<HTMLVideoElement | null>(null);
  const remoteVideoRef = useRef<HTMLVideoElement | null>(null);
  const localStreamRef = useRef<MediaStream | null>(null);
  const remoteStreamRef = useRef<MediaStream>(new MediaStream());
  const peerRef = useRef<RTCPeerConnection | null>(null);
  const processedSignals = useRef(new Set<string>());
  const deferredSignals = useRef<SupportCallSignal[]>([]);
  const deferredIce = useRef<RTCIceCandidateInit[]>([]);
  const offerSentRef = useRef(false);
  const iceRestartRef = useRef(false);
  const closedRef = useRef(false);

  const sendSignal = useCallback(async (signalType: SupportCallSignal["signal_type"], payload: Record<string, unknown>) => {
    await supportApi.sendCallSignal(initialCall.id, signalType, payload);
  }, [initialCall.id]);

  const processSignal = useCallback(async (signal: SupportCallSignal) => {
    if (processedSignals.current.has(signal.signal_id)) return;
    const peer = peerRef.current;
    if (!peer) {
      if (!deferredSignals.current.some((item) => item.signal_id === signal.signal_id)) deferredSignals.current.push(signal);
      return;
    }
    try {
      if (signal.signal_type === "offer" && signal.payload.sdp) {
        await peer.setRemoteDescription({ type: "offer", sdp: String(signal.payload.sdp) });
        for (const candidate of deferredIce.current.splice(0)) await peer.addIceCandidate(candidate);
        const answer = await peer.createAnswer();
        await peer.setLocalDescription(answer);
        await sendSignal("answer", { sdp: answer.sdp, type: answer.type });
      } else if (signal.signal_type === "answer" && signal.payload.sdp) {
        await peer.setRemoteDescription({ type: "answer", sdp: String(signal.payload.sdp) });
        for (const candidate of deferredIce.current.splice(0)) await peer.addIceCandidate(candidate);
      } else if (signal.signal_type === "ice_candidate" && signal.payload.candidate) {
        const candidate: RTCIceCandidateInit = {
          candidate: String(signal.payload.candidate),
          sdpMid: signal.payload.sdpMid == null ? null : String(signal.payload.sdpMid),
          sdpMLineIndex: signal.payload.sdpMLineIndex == null ? null : Number(signal.payload.sdpMLineIndex),
        };
        if (!peer.remoteDescription) deferredIce.current.push(candidate);
        else await peer.addIceCandidate(candidate);
      } else if (signal.signal_type === "renegotiate" && peer.signalingState === "stable") {
        const offer = await peer.createOffer();
        await peer.setLocalDescription(offer);
        await sendSignal("offer", { sdp: offer.sdp, type: offer.type });
      } else if (signal.signal_type === "hangup") {
        setCall((current) => ({ ...current, status: "ended" }));
      }
      processedSignals.current.add(signal.signal_id);
    } catch (signalError) {
      setError(errorMessage(signalError));
    }
  }, [sendSignal]);

  const createOffer = useCallback(async () => {
    const peer = peerRef.current;
    if (!peer || offerSentRef.current) return;
    offerSentRef.current = true;
    try {
      const offer = await peer.createOffer({ offerToReceiveAudio: true, offerToReceiveVideo: call.call_type === "video" });
      await peer.setLocalDescription(offer);
      await sendSignal("offer", { sdp: offer.sdp, type: offer.type });
    } catch (offerError) {
      offerSentRef.current = false;
      setError(errorMessage(offerError));
    }
  }, [call.call_type, sendSignal]);

  useEffect(() => {
    if (call.initiator_kind === "visitor" && call.status === "ringing") return;
    let cancelled = false;
    const initialize = async () => {
      try {
        const credentials = await supportApi.getCallTurnCredentials();
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
          video: call.call_type === "video" ? { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: "user" } : false,
        });
        if (cancelled) { stream.getTracks().forEach((track) => track.stop()); return; }
        localStreamRef.current = stream;
        if (localVideoRef.current) localVideoRef.current.srcObject = stream;
        const peer = new RTCPeerConnection({ iceServers: credentials.ice_servers || [] });
        peerRef.current = peer;
        stream.getTracks().forEach((track) => peer.addTrack(track, stream));
        peer.onicecandidate = (event) => {
          if (!event.candidate) return;
          void sendSignal("ice_candidate", {
            candidate: event.candidate.candidate,
            sdpMid: event.candidate.sdpMid,
            sdpMLineIndex: event.candidate.sdpMLineIndex,
          }).catch(() => undefined);
        };
        peer.ontrack = (event) => {
          const target = remoteStreamRef.current;
          event.streams[0]?.getTracks().forEach((track) => {
            if (!target.getTracks().some((current) => current.id === track.id)) target.addTrack(track);
          });
          if (remoteVideoRef.current) remoteVideoRef.current.srcObject = target;
          setRemoteConnected(true);
        };
        peer.onconnectionstatechange = () => {
          if (["failed", "disconnected"].includes(peer.connectionState)) setError("The connection is unstable. Trying to recover…");
          if (peer.connectionState === "failed" && !iceRestartRef.current) {
            iceRestartRef.current = true;
            void peer.createOffer({ iceRestart: true }).then(async (offer) => {
              await peer.setLocalDescription(offer);
              await sendSignal("offer", { sdp: offer.sdp, type: offer.type, ice_restart: true });
            }).catch(() => undefined).finally(() => { iceRestartRef.current = false; });
          }
          if (peer.connectionState === "connected") { setError(""); setRemoteConnected(true); }
        };
        if (call.initiator_kind === "team") await createOffer();
        const queued = deferredSignals.current.splice(0);
        for (const signal of queued) await processSignal(signal);
      } catch (mediaError) {
        setError(errorMessage(mediaError));
        try {
          setCall(await supportApi.endCall(initialCall.id, "media_unavailable"));
        } catch {
          setCall((current) => ({ ...current, status: "failed", ended_reason: "media_unavailable" }));
        }
      }
    };
    void initialize();
    return () => { cancelled = true; };
  }, [call.call_type, call.initiator_kind, call.status, createOffer, processSignal, sendSignal]);

  useEffect(() => supportSocket.subscribe((event: SupportSocketEvent) => {
    if (String(event.data?.call_id || event.data?.id || "") !== initialCall.id) return;
    if (event.event === "support.call.signal" && event.data?.signal) void processSignal(event.data.signal as unknown as SupportCallSignal);
    if (["support.call.accepted", "support.call.media_updated", "support.call.ended"].includes(event.event)) {
      setCall((current) => ({ ...current, ...(event.data as unknown as Partial<SupportCall>) }));
    }
  }), [initialCall.id, processSignal]);

  useEffect(() => {
    const poll = window.setInterval(() => {
      if (supportSocket.isOpen()) return;
      void supportApi.getCall(initialCall.id).then((payload) => {
        setCall(payload);
        (payload.pending_signals || []).forEach((signal) => void processSignal(signal));
      }).catch(() => undefined);
    }, 1800);
    return () => window.clearInterval(poll);
  }, [initialCall.id, processSignal]);

  const cleanup = useCallback(() => {
    if (closedRef.current) return;
    closedRef.current = true;
    peerRef.current?.close();
    peerRef.current = null;
    localStreamRef.current?.getTracks().forEach((track) => track.stop());
    localStreamRef.current = null;
    remoteStreamRef.current.getTracks().forEach((track) => track.stop());
    onFinished();
  }, [onFinished]);

  useEffect(() => {
    if (!terminal(call.status)) return;
    const timer = window.setTimeout(cleanup, 900);
    return () => window.clearTimeout(timer);
  }, [call.status, cleanup]);

  useEffect(() => () => {
    peerRef.current?.close();
    localStreamRef.current?.getTracks().forEach((track) => track.stop());
    remoteStreamRef.current.getTracks().forEach((track) => track.stop());
  }, []);

  const toggleMute = async () => {
    const next = !muted;
    localStreamRef.current?.getAudioTracks().forEach((track) => { track.enabled = !next; });
    setMuted(next);
    await supportApi.updateCallMedia(call.id, { audio_enabled: !next }).catch(() => undefined);
  };

  const toggleCamera = async () => {
    const next = !cameraEnabled;
    localStreamRef.current?.getVideoTracks().forEach((track) => { track.enabled = next; });
    setCameraEnabled(next);
    await supportApi.updateCallMedia(call.id, { video_enabled: next }).catch(() => undefined);
  };

  const end = async () => {
    try { await sendSignal("hangup", { reason: "ended" }); } catch { /* REST end still closes */ }
    try { setCall(await supportApi.endCall(call.id)); } catch { cleanup(); }
  };

  const acceptIncoming = async () => {
    setError("");
    try {
      await supportApi.acceptCall(call.id);
      const accepted = await supportApi.getCall(call.id);
      (accepted.pending_signals || []).forEach((signal) => {
        if (!deferredSignals.current.some((item) => item.signal_id === signal.signal_id)) {
          deferredSignals.current.push(signal);
        }
      });
      setCall(accepted);
    } catch (acceptError) {
      setError(errorMessage(acceptError));
    }
  };

  const declineIncoming = async () => {
    try {
      setCall(await supportApi.declineCall(call.id));
    } catch (declineError) {
      setError(errorMessage(declineError));
    }
  };

  if (call.initiator_kind === "visitor" && call.status === "ringing") {
    return (
      <div className="ms-support-call-overlay" role="dialog" aria-modal="true" aria-label={`Incoming support call from ${call.visitor_name}`}>
        <section className={`ms-support-call-stage is-${call.call_type}`}>
          <div className="ms-support-call-audio">
            <span>{call.visitor_name.slice(0, 1).toUpperCase()}</span>
            <strong>{call.visitor_name}</strong>
            <small>Incoming {call.call_type === "video" ? "video" : "audio"} call · {call.website_name}</small>
          </div>
          {error ? <div className="ms-support-call-error" role="alert">{error}</div> : null}
          <footer>
            <button type="button" className="is-end" onClick={declineIncoming}>Decline</button>
            <button type="button" onClick={acceptIncoming}>Accept</button>
          </footer>
        </section>
      </div>
    );
  }

  return (
    <div className="ms-support-call-overlay" role="dialog" aria-modal="true" aria-label={`Support call with ${call.visitor_name}`}>
      <section className={`ms-support-call-stage is-${call.call_type}`}>
        <header>
          <div><strong>{call.visitor_name}</strong><span>{call.website_name} · {call.status === "ringing" ? "Calling…" : call.status}</span></div>
        </header>
        <div className="ms-support-call-media">
          {call.call_type === "video" ? (
            <>
              <video ref={remoteVideoRef} autoPlay playsInline className="ms-support-call-remote" />
              {!remoteConnected ? <div className="ms-support-call-placeholder"><span>{call.visitor_name.slice(0, 1).toUpperCase()}</span><strong>{call.status === "ringing" ? "Waiting for visitor" : "Connecting video"}</strong></div> : null}
              <video ref={localVideoRef} autoPlay playsInline muted className="ms-support-call-local" />
            </>
          ) : (
            <div className="ms-support-call-audio"><span>{call.visitor_name.slice(0, 1).toUpperCase()}</span><strong>{call.visitor_name}</strong><small>{call.status === "ringing" ? "Calling visitor…" : remoteConnected ? "Connected" : "Connecting audio…"}</small></div>
          )}
        </div>
        {error ? <div className="ms-support-call-error" role="alert">{error}</div> : null}
        <footer>
          <button type="button" className={muted ? "is-off" : ""} onClick={toggleMute}>{muted ? "Unmute" : "Mute"}</button>
          {call.call_type === "video" ? <button type="button" className={!cameraEnabled ? "is-off" : ""} onClick={toggleCamera}>{cameraEnabled ? "Camera off" : "Camera on"}</button> : null}
          <button type="button" className="is-end" onClick={end}>End call</button>
        </footer>
      </section>
    </div>
  );
}
