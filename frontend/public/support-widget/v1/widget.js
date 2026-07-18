(function () {
  "use strict";

  var script = document.currentScript || document.querySelector("script[data-support-site-key]");
  if (!script || script.dataset.supportMounted === "true") return;
  script.dataset.supportMounted = "true";
  var siteKey = String(script.getAttribute("data-support-site-key") || "").trim();
  if (!siteKey) return;

  var scriptUrl;
  try { scriptUrl = new URL(script.src, window.location.href); } catch (_) { return; }
  var apiBase = scriptUrl.origin + "/api/v1/support/widget/" + encodeURIComponent(siteKey);
  var wsProtocol = scriptUrl.protocol === "https:" ? "wss:" : "ws:";
  var wsBase = wsProtocol + "//" + scriptUrl.host + "/ws/support/widget/" + encodeURIComponent(siteKey) + "/";
  var storageKey = "crescentsupport.session." + siteKey;
  var state = { config: null, session: null, token: "", messages: [], csat: null, csatRating: 0, csatComment: "", csatSubmitting: false, deletionSubmitting: false, deletionRequested: false, knowledge: { enabled: false, categories: [], articles: [], allow_feedback: false }, knowledgeQuery: "", selectedArticle: null, knowledgeLoading: false, open: false, closing: false, closeTimer: 0, loading: false, uploading: false, error: "", timer: 0, socket: null, socketState: "closed", reconnectTimer: 0, reconnectAttempts: 0, heartbeatTimer: 0, hasUnread: false, pendingUploads: [], draft: "", teamTyping: false, typingStopTimer: 0, lastActivityUrl: "", lastReceiptAckId: "", lastReceiptAckStatus: "", recorder: null, recording: false, recordingStartedAt: 0, recordingChunks: [], objectUrls: [], followLatest: true, call: null, callStarting: false, callPeer: null, callLocalStream: null, callRemoteStream: null, callSignalTimer: 0, callSeenSignals: {}, callDeferredSignals: [], callDeferredIce: [] };
  var host = null;
  var shadow = null;

  function request(path, options) {
    var init = options || {};
    var headers = Object.assign({ "Content-Type": "application/json" }, init.headers || {});
    if (state.token) headers.Authorization = "Bearer " + state.token;
    return fetch(apiBase + path, Object.assign({}, init, { mode: "cors", credentials: "omit", cache: "no-store", headers: headers }))
      .then(function (response) {
        if (response.status === 204) return {};
        return response.json().catch(function () { return {}; }).then(function (payload) {
          if (!response.ok) {
            var error = new Error(payload.detail || "Support Chat request failed.");
            error.code = payload.code || "request_failed";
            error.status = response.status;
            throw error;
          }
          return payload;
        });
      });
  }

  function uploadRequest(path, file, metadata) {
    var form = new FormData();
    form.append("file", file);
    form.append("original_name", file.name || "upload");
    if (file.type) form.append("mime_type", file.type);
    if (metadata && typeof metadata.durationSeconds === "number") form.append("duration_seconds", metadata.durationSeconds.toFixed(2));
    var headers = {};
    if (state.token) headers.Authorization = "Bearer " + state.token;
    return fetch(apiBase + path, { method: "POST", mode: "cors", credentials: "omit", cache: "no-store", headers: headers, body: form })
      .then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (payload) {
          if (!response.ok) {
            var error = new Error(payload.detail || "The file could not be uploaded.");
            error.code = payload.code || "upload_failed";
            throw error;
          }
          return payload;
        });
      });
  }

  function authorizedBlob(url) {
    var headers = {};
    if (state.token) headers.Authorization = "Bearer " + state.token;
    return fetch(url, { method: "GET", mode: "cors", credentials: "omit", cache: "no-store", headers: headers }).then(function (response) {
      if (!response.ok) throw new Error("Media could not be loaded.");
      return response.blob();
    });
  }

  function clearObjectUrls() {
    state.objectUrls.forEach(function (url) { try { URL.revokeObjectURL(url); } catch (_) {} });
    state.objectUrls = [];
  }

  function uploadConversationFile(file, metadata) {
    return uploadRequest(sessionPath("/conversation/uploads/"), file, metadata || {});
  }

  function storeSession(payload) {
    state.session = payload || null;
    state.token = payload && payload.token ? payload.token : state.token;
    if (!state.session || !state.token) return;
    try { localStorage.setItem(storageKey, JSON.stringify({ id: state.session.id, token: state.token })); } catch (_) {}
  }

  function clearSession() {
    state.session = null;
    state.token = "";
    state.messages = [];
    state.csat = null;
    state.csatRating = 0;
    state.csatComment = "";
    state.hasUnread = false;
    cleanupCall(true);
    try { localStorage.removeItem(storageKey); } catch (_) {}
  }

  function savedSession() {
    try {
      var value = JSON.parse(localStorage.getItem(storageKey) || "null");
      return value && value.id && value.token ? value : null;
    } catch (_) { return null; }
  }

  function sessionPath(suffix) {
    if (!state.session) throw new Error("No active Support Chat visitor session.");
    return "/sessions/" + encodeURIComponent(state.session.id) + (suffix || "");
  }

  function resumeSession() {
    var saved = savedSession();
    if (!saved) return Promise.resolve(null);
    state.token = saved.token;
    return request("/sessions/" + encodeURIComponent(saved.id) + "/", { method: "GET" })
      .then(function (payload) { state.session = payload; connectRealtime(); return payload; })
      .catch(function () { clearSession(); return null; });
  }

  function loadMessages() {
    if (!state.session) return Promise.resolve([]);
    return request(sessionPath("/conversation/messages/"), { method: "GET" }).then(function (payload) {
      var nextMessages = Array.isArray(payload.messages) ? payload.messages : [];
      var changed = JSON.stringify(nextMessages) !== JSON.stringify(state.messages);
      state.messages = nextMessages;
      acknowledgeLatestTeamMessage();
      if (changed) {
        render();
        if (state.followLatest) scrollMessages();
      }
      return state.messages;
    });
  }

  function sendRealtime(event, data) {
    if (!state.socket || state.socket.readyState !== WebSocket.OPEN) return false;
    try { state.socket.send(JSON.stringify({ event: event, data: data || {} })); return true; } catch (_) { return false; }
  }

  function latestTeamMessage() {
    for (var index = state.messages.length - 1; index >= 0; index -= 1) {
      if (!state.messages[index].sender || state.messages[index].sender.kind !== "visitor") return state.messages[index];
    }
    return null;
  }

  function acknowledgeLatestTeamMessage() {
    var message = latestTeamMessage();
    if (!message || String(message.id || "").indexOf("temp-") === 0) return;
    var currentStatus = String(message.receipt_status || message.delivery_status || "sent").toLowerCase();
    var targetStatus = state.open && document.visibilityState === "visible" ? "read" : "delivered";
    var rank = { pending: 0, sent: 1, delivered: 2, read: 3 };
    if ((rank[currentStatus] || 0) >= rank[targetStatus]) return;
    if (state.lastReceiptAckId === String(message.id) && (rank[state.lastReceiptAckStatus] || 0) >= rank[targetStatus]) return;
    var sent = sendRealtime(targetStatus === "read" ? "support.message.read" : "support.message.delivered", { message_id: message.id });
    if (sent) {
      state.lastReceiptAckId = String(message.id);
      state.lastReceiptAckStatus = targetStatus;
    }
  }

  function reportVisitorActivity(force) {
    if (!state.session) return;
    var currentUrl = window.location.href;
    if (!force && state.lastActivityUrl === currentUrl) {
      sendRealtime("support.visitor.activity", { current_page_url: currentUrl, referrer: document.referrer || "" });
      return;
    }
    state.lastActivityUrl = currentUrl;
    if (!sendRealtime("support.visitor.activity", { current_page_url: currentUrl, referrer: document.referrer || "" })) {
      api.updateSession({ current_page_url: currentUrl, referrer: document.referrer || "" }).catch(function () {});
    }
  }

  function reportVisitorTyping(active) {
    sendRealtime(active ? "support.typing.start" : "support.typing.stop", {});
    if (state.typingStopTimer) window.clearTimeout(state.typingStopTimer);
    state.typingStopTimer = 0;
    if (active) state.typingStopTimer = window.setTimeout(function () { reportVisitorTyping(false); }, 1800);
  }

  function knowledgeClientKey() {
    var key = "";
    try { key = localStorage.getItem(storageKey + ".knowledge") || ""; } catch (_) {}
    if (key) return key;
    if (window.crypto && typeof window.crypto.randomUUID === "function") key = window.crypto.randomUUID();
    else key = "kb-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
    try { localStorage.setItem(storageKey + ".knowledge", key); } catch (_) {}
    return key;
  }

  function loadKnowledge(query, category) {
    if (!state.config || !state.config.knowledge_enabled) return Promise.resolve(null);
    state.knowledgeLoading = true;
    var params = [];
    if (query) params.push("q=" + encodeURIComponent(query));
    if (category) params.push("category=" + encodeURIComponent(category));
    return request("/knowledge/" + (params.length ? "?" + params.join("&") : ""), { method: "GET" }).then(function (payload) {
      state.knowledge = payload || { enabled: false, categories: [], articles: [], allow_feedback: false };
      state.knowledgeLoading = false; render(); return payload;
    }).catch(function () { state.knowledgeLoading = false; return null; });
  }

  function loadKnowledgeArticle(articleId) {
    state.knowledgeLoading = true; state.error = ""; render();
    return request("/knowledge/articles/" + encodeURIComponent(articleId) + "/", { method: "GET" }).then(function (payload) {
      state.selectedArticle = payload; state.knowledgeLoading = false; render(); return payload;
    }).catch(function (error) { state.knowledgeLoading = false; state.error = error.message || "The article could not be loaded."; render(); throw error; });
  }

  function submitKnowledgeFeedback(articleId, helpful) {
    return request("/knowledge/articles/" + encodeURIComponent(articleId) + "/feedback/", { method: "POST", body: JSON.stringify({ helpful: Boolean(helpful), client_key: knowledgeClientKey() }) }).then(function (payload) {
      if (state.selectedArticle && state.selectedArticle.id === articleId) state.selectedArticle.__feedback = helpful ? "helpful" : "not_helpful";
      render(); return payload;
    });
  }

  function loadCSAT() {
    if (!state.session) return Promise.resolve(null);
    return request(sessionPath("/conversation/csat/"), { method: "GET" }).then(function (payload) {
      var nextCSAT = payload || null;
      var changed = JSON.stringify(nextCSAT) !== JSON.stringify(state.csat);
      state.csat = nextCSAT;
      if (changed) render();
      return state.csat;
    });
  }

  function submitCSAT(rating, comment) {
    state.csatSubmitting = true; state.error = ""; render();
    return request(sessionPath("/conversation/csat/"), { method: "POST", body: JSON.stringify({ rating: rating, comment: comment || "" }) }).then(function (payload) {
      state.csat = { enabled: true, allow_comment: state.csat ? state.csat.allow_comment : true, survey: payload.survey };
      state.csatSubmitting = false; state.csatRating = 0; state.csatComment = ""; render();
      return payload;
    }).catch(function (error) {
      state.csatSubmitting = false; state.error = error.message || "Feedback could not be submitted."; render(); throw error;
    });
  }

  function dismissCSAT() {
    return request(sessionPath("/conversation/csat/"), { method: "DELETE" }).then(function (payload) {
      state.csat = { enabled: true, allow_comment: state.csat ? state.csat.allow_comment : true, survey: payload.survey };
      render(); return payload;
    });
  }

  function requestVisitorDeletion() {
    if (!state.session || state.deletionSubmitting) return Promise.resolve(null);
    state.deletionSubmitting = true; state.error = ""; render();
    return request(sessionPath("/privacy/delete/"), { method: "POST" }).then(function (payload) {
      state.deletionSubmitting = false;
      state.deletionRequested = true;
      closeRealtime(true);
      clearSession();
      render();
      return payload;
    }).catch(function (error) {
      state.deletionSubmitting = false; state.error = error.message || "Your deletion request could not be submitted."; render(); throw error;
    });
  }

  function sendMessage(text, attachmentIds, voiceNote) {
    var body = String(text || "").trim();
    var uploads = Array.isArray(attachmentIds) ? attachmentIds : [];
    if (!body && !uploads.length) return Promise.reject(new Error("Write a message or add an attachment before sending."));
    var clientTempId = "temp-" + Date.now().toString(36) + Math.random().toString(36).slice(2);
    var optimisticUploads = state.pendingUploads.filter(function (upload) { return uploads.indexOf(upload.id) >= 0; });
    state.messages.push({
      id: clientTempId,
      type: optimisticUploads[0] ? optimisticUploads[0].kind : "text",
      text: body,
      created_at: new Date().toISOString(),
      delivery_status: "pending",
      receipt_status: "pending",
      sender: { kind: "visitor", display_name: "You" },
      attachments: optimisticUploads.map(function (upload) {
        return { id: upload.id, media_kind: upload.kind, original_name: upload.name, mime_type: "", size: 0, scan_status: "clean", can_preview_inline: false, download_url: "" };
      })
    });
    state.draft = "";
    render();
    scrollMessages();
    return request(sessionPath("/conversation/messages/"), { method: "POST", body: JSON.stringify({ text: body, attachment_ids: uploads, voice_note: Boolean(voiceNote) }) })
      .then(function (payload) {
        state.messages = state.messages.filter(function (message) { return message.id !== clientTempId; });
        if (payload.message && !state.messages.some(function (message) { return message.id === payload.message.id; })) state.messages.push(payload.message);
        state.pendingUploads = [];
        render();
        scrollMessages();
        return payload;
      }).catch(function (error) {
        state.messages = state.messages.map(function (message) {
          if (message.id !== clientTempId) return message;
          message.delivery_status = "failed";
          message.receipt_status = "failed";
          return message;
        });
        throw error;
      });
  }

  function addPendingFiles(files) {
    var remaining = Math.max(0, 8 - state.pendingUploads.length);
    var selected = Array.prototype.slice.call(files || [], 0, remaining);
    if (!selected.length) return;
    state.uploading = true;
    state.error = "";
    render();
    Promise.all(selected.map(function (file) {
      return uploadConversationFile(file).then(function (upload) {
        state.pendingUploads.push({ id: upload.id, name: upload.original_name || file.name, kind: upload.media_kind });
      });
    })).catch(function (error) {
      state.error = error.message || "A file could not be uploaded.";
    }).then(function () {
      state.uploading = false;
      render();
    });
  }

  function stopVoiceRecording(sendAfterStop) {
    if (!state.recorder) return;
    state.recorder.__sendAfterStop = Boolean(sendAfterStop);
    try { state.recorder.stop(); } catch (_) {}
  }

  function toggleVoiceRecording() {
    if (state.recording) { stopVoiceRecording(true); return; }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || typeof MediaRecorder === "undefined") {
      state.error = "Voice recording is not supported in this browser."; render(); return;
    }
    state.error = "";
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      var recorder = new MediaRecorder(stream);
      state.recorder = recorder;
      state.recordingChunks = [];
      state.recordingStartedAt = Date.now();
      recorder.ondataavailable = function (event) { if (event.data && event.data.size) state.recordingChunks.push(event.data); };
      recorder.onstop = function () {
        var shouldSend = Boolean(recorder.__sendAfterStop);
        var durationSeconds = Math.max(0.1, (Date.now() - state.recordingStartedAt) / 1000);
        stream.getTracks().forEach(function (track) { track.stop(); });
        state.recording = false; state.recorder = null;
        if (!shouldSend || !state.recordingChunks.length) { render(); return; }
        var mime = recorder.mimeType || "audio/webm";
        var extension = mime.indexOf("ogg") >= 0 ? "ogg" : mime.indexOf("mp4") >= 0 ? "m4a" : "webm";
        var file = new File([new Blob(state.recordingChunks, { type: mime })], "voice-" + Date.now() + "." + extension, { type: mime });
        state.uploading = true; render();
        uploadConversationFile(file, { durationSeconds: durationSeconds }).then(function (upload) {
          return sendMessage("", [upload.id], true);
        }).catch(function (error) {
          state.error = error.message || "The voice message could not be sent.";
        }).then(function () { state.uploading = false; render(); });
      };
      recorder.start(250);
      state.recording = true;
      render();
    }).catch(function () { state.error = "Microphone access is required to record a voice message."; render(); });
  }


  function callTerminal(call) {
    return call && ["declined", "missed", "ended", "failed"].indexOf(call.status) >= 0;
  }

  function cleanupCall(clearState) {
    if (state.callSignalTimer) window.clearInterval(state.callSignalTimer);
    state.callSignalTimer = 0;
    try { if (state.callPeer) state.callPeer.close(); } catch (_) {}
    state.callPeer = null;
    try { if (state.callLocalStream) state.callLocalStream.getTracks().forEach(function (track) { track.stop(); }); } catch (_) {}
    try { if (state.callRemoteStream) state.callRemoteStream.getTracks().forEach(function (track) { track.stop(); }); } catch (_) {}
    state.callLocalStream = null;
    state.callRemoteStream = null;
    state.callSeenSignals = {};
    state.callDeferredSignals = [];
    state.callDeferredIce = [];
    if (clearState !== false) state.call = null;
  }

  function callPath(callId, suffix) {
    return sessionPath("/calls/" + encodeURIComponent(callId) + (suffix || "/"));
  }

  function sendCallSignal(type, payload) {
    if (!state.call) return Promise.reject(new Error("No active call."));
    return request(callPath(state.call.id, "/signals/"), { method: "POST", body: JSON.stringify({ signal_type: type, payload: payload || {} }) });
  }

  function processCallSignal(signal) {
    if (!signal || !signal.signal_id || state.callSeenSignals[signal.signal_id]) return Promise.resolve();
    var peer = state.callPeer;
    if (!peer) {
      if (!state.callDeferredSignals.some(function (item) { return item.signal_id === signal.signal_id; })) state.callDeferredSignals.push(signal);
      return Promise.resolve();
    }
    var payload = signal.payload || {};
    var action = Promise.resolve();
    if (signal.signal_type === "offer" && payload.sdp) {
      action = peer.setRemoteDescription({ type: "offer", sdp: String(payload.sdp) }).then(function () {
        var candidates = state.callDeferredIce.splice(0);
        return Promise.all(candidates.map(function (candidate) { return peer.addIceCandidate(candidate); }));
      }).then(function () {
        return peer.createAnswer();
      }).then(function (answer) {
        return peer.setLocalDescription(answer).then(function () { return sendCallSignal("answer", { type: answer.type, sdp: answer.sdp }); });
      });
    } else if (signal.signal_type === "ice_candidate" && payload.candidate) {
      var candidate = { candidate: String(payload.candidate), sdpMid: payload.sdpMid == null ? null : String(payload.sdpMid), sdpMLineIndex: payload.sdpMLineIndex == null ? null : Number(payload.sdpMLineIndex) };
      if (!peer.remoteDescription) state.callDeferredIce.push(candidate);
      else action = peer.addIceCandidate(candidate);
    } else if (signal.signal_type === "hangup") {
      state.call.status = "ended"; cleanupCall(false); render();
    }
    return action.then(function () { state.callSeenSignals[signal.signal_id] = true; });
  }

  function pollCallSignals() {
    if (state.socketState === "open" || !state.call || !state.callPeer || callTerminal(state.call)) return;
    request(callPath(state.call.id, "/signals/"), { method: "GET" }).then(function (payload) {
      (payload.signals || []).forEach(function (signal) { processCallSignal(signal).catch(function () {}); });
    }).catch(function () {});
  }

  function bindCallStreams() {
    if (!shadow) return;
    var local = shadow.querySelector(".cs-call-local");
    var remote = shadow.querySelector(".cs-call-remote");
    if (local && state.callLocalStream && local.srcObject !== state.callLocalStream) local.srcObject = state.callLocalStream;
    if (remote && state.callRemoteStream && remote.srcObject !== state.callRemoteStream) remote.srcObject = state.callRemoteStream;
  }

  function beginVisitorCall() {
    if (!state.call || state.callPeer) return Promise.resolve(state.call);
    return Promise.all([
      request(sessionPath("/calls/turn-credentials/"), { method: "GET" }),
      navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: state.call.call_type === "video" ? { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: "user" } : false })
    ]).then(function (results) {
      var credentials = results[0] || {};
      var stream = results[1];
      state.callLocalStream = stream;
      state.callRemoteStream = new MediaStream();
      var peer = new RTCPeerConnection({ iceServers: credentials.ice_servers || [] });
      state.callPeer = peer;
      stream.getTracks().forEach(function (track) { peer.addTrack(track, stream); });
      peer.onicecandidate = function (event) {
        if (!event.candidate) return;
        sendCallSignal("ice_candidate", { candidate: event.candidate.candidate, sdpMid: event.candidate.sdpMid, sdpMLineIndex: event.candidate.sdpMLineIndex }).catch(function () {});
      };
      peer.ontrack = function (event) {
        (event.streams[0] ? event.streams[0].getTracks() : [event.track]).forEach(function (track) {
          if (!state.callRemoteStream.getTracks().some(function (item) { return item.id === track.id; })) state.callRemoteStream.addTrack(track);
        });
        bindCallStreams();
      };
      peer.onconnectionstatechange = function () {
        if (peer.connectionState === "failed") state.error = "The call connection failed.";
        if (peer.connectionState === "connected") state.error = "";
        render();
      };
      var queuedSignals = (state.call.pending_signals || []).concat(state.callDeferredSignals.splice(0));
      queuedSignals.forEach(function (signal) { processCallSignal(signal).catch(function () {}); });
      if (state.call.initiator_kind === "visitor" && !peer.localDescription) {
        peer.createOffer({ offerToReceiveAudio: true, offerToReceiveVideo: state.call.call_type === "video" })
          .then(function (offer) {
            return peer.setLocalDescription(offer).then(function () {
              return sendCallSignal("offer", { type: offer.type, sdp: offer.sdp });
            });
          })
          .catch(function (error) { state.error = error.message || "The call could not connect."; render(); });
      }
      state.callSignalTimer = window.setInterval(pollCallSignals, 1200);
      render(); window.requestAnimationFrame(bindCallStreams);
      return state.call;
    });
  }

  function loadActiveCall() {
    if (!state.session || !state.config || !state.config.calls_enabled) return Promise.resolve(null);
    return request(sessionPath("/calls/active/"), { method: "GET" }).then(function (payload) {
      var next = payload.call || null;
      if (!next) {
        if (state.call && callTerminal(state.call)) cleanupCall(true);
        return null;
      }
      state.call = next;
      (next.pending_signals || []).forEach(function (signal) { processCallSignal(signal).catch(function () {}); });
      if (next.status === "ongoing" && !state.callPeer) beginVisitorCall().catch(function (error) { state.error = error.message || "The call could not start."; render(); });
      render();
      return next;
    }).catch(function () { return null; });
  }

  function acceptIncomingCall() {
    if (!state.call) return;
    state.error = "";
    request(callPath(state.call.id, "/accept/"), { method: "POST" }).then(function (payload) {
      state.call = payload;
      return beginVisitorCall();
    }).catch(function (error) {
      state.error = error.message || "The call could not be accepted.";
      var activeCallId = state.call && state.call.id;
      var finish = activeCallId ? request(callPath(activeCallId, "/end/"), { method: "POST", body: JSON.stringify({ reason: "media_unavailable" }) }).catch(function () {}) : Promise.resolve();
      finish.then(function () { cleanupCall(true); render(); });
    });
  }

  function startVisitorCall(callType) {
    if (!state.session || state.callStarting || (state.call && !callTerminal(state.call))) return;
    state.callStarting = true;
    state.error = "";
    render();
    request(sessionPath("/calls/"), {
      method: "POST",
      body: JSON.stringify({ call_type: callType }),
    }).then(function (payload) {
      state.call = payload;
      state.callStarting = false;
      state.open = true;
      render();
      return beginVisitorCall();
    }).catch(function (error) {
      state.callStarting = false;
      state.error = error.message || "The support call could not be started.";
      var callId = state.call && state.call.id;
      var cleanup = callId
        ? request(callPath(callId, "/end/"), { method: "POST", body: JSON.stringify({ reason: "media_unavailable" }) }).catch(function () {})
        : Promise.resolve();
      cleanup.then(function () { cleanupCall(true); render(); });
    });
  }

  function declineIncomingCall() {
    if (!state.call) return;
    request(callPath(state.call.id, "/decline/"), { method: "POST", body: JSON.stringify({ reason: "declined" }) }).catch(function () {}).then(function () { cleanupCall(true); render(); });
  }

  function endVisitorCall() {
    if (!state.call) return;
    var callId = state.call.id;
    sendCallSignal("hangup", { reason: "ended" }).catch(function () {});
    request(callPath(callId, "/end/"), { method: "POST", body: JSON.stringify({ reason: "ended" }) }).catch(function () {}).then(function () { cleanupCall(true); render(); });
  }

  function renderCall(body) {
    if (!state.call || callTerminal(state.call)) return false;
    if (state.call.status === "ringing" && state.call.initiator_kind !== "visitor") {
      var incoming = node("section", "cs-call-incoming");
      incoming.appendChild(node("span", "cs-call-avatar", (state.call.initiated_by.display_name || "S").slice(0,1).toUpperCase()));
      incoming.appendChild(node("strong", "", state.call.initiated_by.display_name || "Support team"));
      incoming.appendChild(node("small", "", "Incoming " + state.call.call_type + " call"));
      var actions = node("div", "cs-call-actions");
      var decline = node("button", "cs-call-decline", "Decline"); decline.type = "button"; decline.onclick = declineIncomingCall;
      var accept = node("button", "cs-call-accept", "Accept"); accept.type = "button"; accept.onclick = acceptIncomingCall;
      actions.appendChild(decline); actions.appendChild(accept); incoming.appendChild(actions); body.appendChild(incoming); return true;
    }
    if (state.call.status === "ringing" && state.call.initiator_kind === "visitor") {
      var outgoing = node("section", "cs-call-incoming");
      outgoing.appendChild(node("span", "cs-call-avatar", (state.config.brand_name || "S").slice(0,1).toUpperCase()));
      outgoing.appendChild(node("strong", "", "Calling " + (state.config.brand_name || "Support")));
      outgoing.appendChild(node("small", "", "Waiting for the support team to answer your " + state.call.call_type + " call"));
      var cancel = node("button", "cs-call-decline", "Cancel call"); cancel.type = "button"; cancel.onclick = endVisitorCall;
      var outgoingActions = node("div", "cs-call-actions"); outgoingActions.appendChild(cancel); outgoing.appendChild(outgoingActions); body.appendChild(outgoing); return true;
    }
    var stage = node("section", "cs-call-stage " + state.call.call_type);
    if (state.call.call_type === "video") {
      var remote = node("video", "cs-call-remote"); remote.autoplay = true; remote.playsInline = true; stage.appendChild(remote);
      var local = node("video", "cs-call-local"); local.autoplay = true; local.playsInline = true; local.muted = true; stage.appendChild(local);
    } else {
      var audio = node("div", "cs-call-audio"); audio.appendChild(node("span", "cs-call-avatar", (state.call.initiated_by.display_name || "S").slice(0,1).toUpperCase())); audio.appendChild(node("strong", "", state.call.initiated_by.display_name || "Support team")); audio.appendChild(node("small", "", "Audio call connected")); stage.appendChild(audio);
      var remoteAudio = node("audio", "cs-call-remote"); remoteAudio.autoplay = true; stage.appendChild(remoteAudio);
    }
    var controls = node("div", "cs-call-controls");
    var mute = node("button", "", "Mute"); mute.type = "button"; mute.onclick = function () { var track = state.callLocalStream && state.callLocalStream.getAudioTracks()[0]; if (track) { track.enabled = !track.enabled; mute.textContent = track.enabled ? "Mute" : "Unmute"; request(callPath(state.call.id, "/media-state/"), { method: "PATCH", body: JSON.stringify({ audio_enabled: track.enabled }) }).catch(function () {}); } };
    controls.appendChild(mute);
    if (state.call.call_type === "video") { var camera = node("button", "", "Camera off"); camera.type = "button"; camera.onclick = function () { var track = state.callLocalStream && state.callLocalStream.getVideoTracks()[0]; if (track) { track.enabled = !track.enabled; camera.textContent = track.enabled ? "Camera off" : "Camera on"; request(callPath(state.call.id, "/media-state/"), { method: "PATCH", body: JSON.stringify({ video_enabled: track.enabled }) }).catch(function () {}); } }; controls.appendChild(camera); }
    var end = node("button", "cs-call-end", "End"); end.type = "button"; end.onclick = endVisitorCall; controls.appendChild(end); stage.appendChild(controls); body.appendChild(stage); window.requestAnimationFrame(bindCallStreams); return true;
  }

  function stopHeartbeat() {
    if (state.heartbeatTimer) window.clearInterval(state.heartbeatTimer);
    state.heartbeatTimer = 0;
  }

  function closeRealtime(manual) {
    if (state.reconnectTimer) window.clearTimeout(state.reconnectTimer);
    state.reconnectTimer = 0;
    stopHeartbeat();
    var socket = state.socket;
    state.socket = null;
    state.socketState = "closed";
    if (manual) state.reconnectAttempts = 0;
    try { if (socket) socket.close(1000, manual ? "support-widget-close" : "support-widget-reconnect"); } catch (_) {}
  }

  function scheduleRealtimeReconnect() {
    if (state.reconnectTimer || !state.session || !state.token) return;
    var delay = Math.min(15000, 750 * Math.pow(2, Math.min(state.reconnectAttempts, 5)));
    state.reconnectAttempts += 1;
    state.reconnectTimer = window.setTimeout(function () { state.reconnectTimer = 0; connectRealtime(); }, delay);
  }

  function connectRealtime() {
    if (!state.session || !state.token || typeof WebSocket === "undefined") { startPolling(); return; }
    if (state.socket && (state.socket.readyState === WebSocket.OPEN || state.socket.readyState === WebSocket.CONNECTING)) return;
    closeRealtime(false);
    state.socketState = "connecting";
    var socket;
    try {
      socket = new WebSocket(wsBase + "?session_id=" + encodeURIComponent(state.session.id) + "&token=" + encodeURIComponent(state.token));
    } catch (_) { state.socketState = "closed"; startPolling(); scheduleRealtimeReconnect(); return; }
    state.socket = socket;
    socket.onopen = function () {
      if (state.socket !== socket) return;
      state.socketState = "open";
      state.reconnectAttempts = 0;
      stopPolling();
      stopHeartbeat();
      state.heartbeatTimer = window.setInterval(function () {
        if (state.socket === socket && socket.readyState === WebSocket.OPEN) reportVisitorActivity(false);
      }, 25000);
      reportVisitorActivity(true);
      if (state.open) render();
      if (state.open) { loadMessages().catch(function () {}); loadCSAT().catch(function () {}); loadActiveCall().catch(function () {}); }
    };
    socket.onmessage = function (event) {
      if (state.socket !== socket) return;
      try {
        var payload = JSON.parse(event.data || "{}");
        if (payload.event === "support.message.created" || payload.event === "support.conversation.updated") {
          var senderKind = payload.data && payload.data.sender ? payload.data.sender.kind : "";
          if (payload.event === "support.message.created" && senderKind !== "visitor" && payload.data && payload.data.id) {
            sendRealtime("support.message.delivered", { message_id: payload.data.id });
            if (state.open && document.visibilityState === "visible") sendRealtime("support.message.read", { message_id: payload.data.id });
          }
          if (!state.open && senderKind !== "visitor") { state.hasUnread = true; render(); return; }
          if (state.open) loadMessages().catch(function () {});
        } else if (payload.event === "support.typing.started" || payload.event === "support.typing.stopped") {
          var teamTyping = payload.event === "support.typing.started";
          if (state.teamTyping !== teamTyping) {
            state.teamTyping = teamTyping;
            render();
          }
        } else if (payload.event === "support.message.delivered" || payload.event === "support.message.read") {
          loadMessages().catch(function () {});
        } else if (payload.event === "support.csat.updated") {
          if (state.open) loadCSAT().catch(function () {});
          else { state.hasUnread = true; render(); }
        } else if (payload.event === "support.call.ringing" || payload.event === "support.call.accepted" || payload.event === "support.call.media_updated") {
          state.call = payload.data || state.call;
          if (state.call && state.call.status === "ongoing" && !state.callPeer) beginVisitorCall().catch(function () {});
          state.open = true; render();
        } else if (payload.event === "support.call.signal") {
          if (payload.data && payload.data.signal) processCallSignal(payload.data.signal).catch(function () {});
        } else if (payload.event === "support.call.ended") {
          cleanupCall(true); render();
        }
      } catch (_) {}
    };
    socket.onerror = function () { if (state.socket === socket) state.socketState = "closed"; };
    socket.onclose = function () {
      if (state.socket !== socket) return;
      state.socket = null; state.socketState = "closed"; stopHeartbeat(); startPolling(); scheduleRealtimeReconnect();
      if (state.open) render();
    };
  }

  function stopPolling() {
    if (state.timer) window.clearInterval(state.timer);
    state.timer = 0;
  }

  function startPolling() {
    stopPolling();
    if (!state.open || !state.session || state.socketState === "open") return;
    state.timer = window.setInterval(function () {
      if (state.open && document.visibilityState === "visible") { loadMessages().catch(function () {}); loadCSAT().catch(function () {}); loadActiveCall().catch(function () {}); }
    }, 5000);
  }

  var api = {
    get config() { return state.config; },
    get session() { return state.session; },
    get messages() { return state.messages.slice(); },
    get feedback() { return state.csat; },
    get knowledge() { return state.knowledge; },
    ready: null,
    createSession: function (visitor) {
      var payload = Object.assign({ locale: navigator.language || "", current_page_url: window.location.href, referrer: document.referrer || "" }, visitor || {});
      return request("/sessions/", { method: "POST", body: JSON.stringify(payload) }).then(function (result) {
        storeSession(result);
        connectRealtime();
        return Promise.all([loadMessages(), loadCSAT(), loadActiveCall()]).then(function () { return result; });
      });
    },
    resumeSession: resumeSession,
    updateSession: function (changes) {
      return request(sessionPath("/"), { method: "PATCH", body: JSON.stringify(changes || {}) }).then(function (result) { state.session = result; render(); return result; });
    },
    refreshSession: function () {
      return request(sessionPath("/refresh/"), { method: "POST" }).then(function (result) { storeSession(result); closeRealtime(false); connectRealtime(); return result; });
    },
    closeSession: function () {
      if (!state.session) { clearSession(); render(); return Promise.resolve(); }
      return request(sessionPath("/"), { method: "DELETE" }).catch(function () {}).then(function () { closeRealtime(true); clearSession(); render(); });
    },
    listMessages: loadMessages,
    loadFeedback: loadCSAT,
    searchKnowledge: loadKnowledge,
    openKnowledgeArticle: loadKnowledgeArticle,
    submitKnowledgeFeedback: submitKnowledgeFeedback,
    submitFeedback: submitCSAT,
    dismissFeedback: dismissCSAT,
    requestDataDeletion: requestVisitorDeletion,
    sendMessage: sendMessage,
    uploadFile: uploadConversationFile,
    markRead: function () { return state.session ? request(sessionPath("/conversation/read/"), { method: "POST" }) : Promise.resolve(); },
    open: function () {
      if (state.closeTimer) window.clearTimeout(state.closeTimer);
      state.closeTimer = 0; state.closing = false; state.open = true; state.hasUnread = false;
      render(); connectRealtime(); reportVisitorActivity(true); acknowledgeLatestTeamMessage();
      if (state.session) { loadMessages().catch(function () {}); loadCSAT().catch(function () {}); loadActiveCall().catch(function () {}); }
      startPolling();
    },
    close: function () {
      if (state.call && !callTerminal(state.call)) return;
      if (!state.open || state.closing) return;
      reportVisitorTyping(false); state.closing = true; render(); stopPolling();
      state.closeTimer = window.setTimeout(function () {
        state.open = false; state.closing = false; state.closeTimer = 0; render();
      }, 150);
    },
    clearSession: function () { closeRealtime(true); clearSession(); render(); }
  };

  function styles() {
    var side = state.config && state.config.position === "left" ? "left" : "right";
    var primary = state.config ? state.config.primary_color : "#111111";
    var dark = state.config && state.config.theme === "dark";
    var surface = dark ? "#171717" : "#ffffff";
    var canvas = dark ? "#101010" : "#f5f5f5";
    var text = dark ? "#f5f5f5" : "#171717";
    var muted = dark ? "#a8a8a8" : "#6b6b6b";
    var line = dark ? "#303030" : "#e2e2e2";
    return `:host{all:initial;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:${text}}*{box-sizing:border-box}.cs-wrap{position:fixed;z-index:2147483000;bottom:max(18px,env(safe-area-inset-bottom));${side}:18px;display:grid;justify-items:${side === "left" ? "start" : "end"};gap:10px}.cs-launcher{position:relative;min-width:54px;height:54px;padding:0 18px;border:0;border-radius:18px;background:${primary};color:#fff;font:800 14px/1 inherit;box-shadow:0 14px 38px rgba(0,0,0,.22);cursor:pointer}.cs-unread{position:absolute;top:-5px;right:-5px;min-width:18px;height:18px;display:grid;place-items:center;padding:0 4px;border:2px solid #fff;border-radius:999px;background:#d92d20;color:#fff;font:800 10px/1 inherit}.cs-panel{width:min(380px,calc(100vw - 24px));height:min(620px,calc(100dvh - 100px));display:${state.open ? "grid" : "none"};grid-template-rows:auto minmax(0,1fr) auto;overflow:hidden;border:1px solid ${line};border-radius:20px;background:${surface};box-shadow:0 24px 70px rgba(0,0,0,.24)}.cs-header{display:flex;align-items:center;gap:12px;padding:14px 16px;border-bottom:1px solid ${line};background:${surface}}.cs-mark{width:36px;height:36px;display:grid;place-items:center;border-radius:11px;background:${primary};color:#fff;font-weight:850}.cs-title{min-width:0;display:grid;flex:1}.cs-title strong,.cs-title small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.cs-title strong{font-size:14px}.cs-title small{color:${muted};font-size:11px}.cs-close{width:34px;height:34px;border:0;border-radius:50%;background:${canvas};color:${text};font-size:20px;cursor:pointer}.cs-body{min-height:0;overflow-y:auto;padding:16px;background:${canvas}}.cs-welcome{margin:0 0 14px;color:${muted};font-size:13px;line-height:1.55}.cs-form{display:grid;gap:10px}.cs-field,.cs-composer textarea{width:100%;min-height:44px;padding:11px 12px;border:1px solid ${line};border-radius:12px;background:${surface};color:${text};font:inherit}.cs-primary{min-height:44px;border:0;border-radius:12px;background:${primary};color:#fff;font:800 13px inherit;cursor:pointer}.cs-error{padding:10px 12px;border:1px solid #d98c8c;border-radius:10px;background:#fff0f0;color:#8d1b1b;font-size:12px}.cs-messages{display:flex;flex-direction:column;gap:12px}.cs-message{max-width:86%;display:grid;gap:3px}.cs-message.visitor{align-self:flex-end;justify-items:end}.cs-message.team{align-self:flex-start;justify-items:start}.cs-meta{color:${muted};font-size:10px}.cs-typing{padding:7px 2px;color:${muted};font:700 11px inherit}.cs-bubble{padding:9px 11px;border:1px solid ${line};border-radius:13px;background:${surface};color:${text};font-size:13px;line-height:1.45;white-space:pre-wrap;overflow-wrap:anywhere}.cs-message.visitor .cs-bubble{border-color:${primary};background:${primary};color:#fff}.cs-empty{padding:30px 10px;color:${muted};font-size:13px;text-align:center}.cs-composer{display:${state.session ? "grid" : "none"};grid-template-columns:minmax(0,1fr) auto;gap:8px;padding:12px;border-top:1px solid ${line};background:${surface}}.cs-composer textarea{min-height:42px;max-height:110px;resize:none}.cs-send{min-width:62px;border:0;border-radius:12px;background:${primary};color:#fff;font:800 12px inherit;cursor:pointer}.cs-note{margin:8px 0 0;color:${muted};font-size:10px;line-height:1.4}.cs-bubble-text:empty{display:none}.cs-media{display:grid;gap:7px;min-width:190px}.cs-media img,.cs-media video{display:block;width:100%;max-height:260px;border-radius:9px;object-fit:contain;background:rgba(0,0,0,.08)}.cs-media audio{width:100%;min-width:210px}.cs-file{display:flex;align-items:center;gap:8px;padding:8px;border:1px solid currentColor;border-radius:9px}.cs-file span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.cs-download{justify-self:start;padding:0;border:0;background:transparent;color:inherit;font:800 10px inherit;text-decoration:underline;cursor:pointer}.cs-composer{grid-template-columns:auto minmax(0,1fr) auto auto}.cs-tool{width:42px;height:42px;border:1px solid ${line};border-radius:12px;background:${surface};color:${text};font:800 18px inherit;cursor:pointer}.cs-tool.recording{border-color:#d92d20;color:#d92d20}.cs-upload-list{grid-column:1/-1;display:flex;gap:6px;overflow-x:auto}.cs-upload-chip{display:flex;align-items:center;gap:5px;max-width:150px;padding:5px 7px;border:1px solid ${line};border-radius:8px;background:${canvas};font-size:10px}.cs-upload-chip span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.cs-upload-chip button{border:0;background:transparent;color:${text};cursor:pointer}.cs-hidden{position:fixed;width:1px;height:1px;opacity:0;pointer-events:none}.cs-csat{display:grid;gap:10px;margin-top:14px;padding:14px;border:1px solid ${line};border-radius:14px;background:${surface}}.cs-csat strong{font-size:13px}.cs-csat p{margin:0;color:${muted};font-size:11px;line-height:1.45}.cs-csat-stars{display:flex;gap:6px}.cs-csat-star{width:36px;height:36px;border:1px solid ${line};border-radius:10px;background:${canvas};color:${text};font-size:20px;cursor:pointer}.cs-csat-star.is-selected{border-color:${primary};background:${primary};color:#fff}.cs-csat textarea{width:100%;min-height:70px;padding:9px 10px;border:1px solid ${line};border-radius:10px;background:${canvas};color:${text};font:inherit;resize:vertical}.cs-csat-actions{display:flex;justify-content:flex-end;gap:8px}.cs-csat-secondary{border:0;background:transparent;color:${muted};font:700 11px inherit;cursor:pointer}.cs-csat-submit{min-height:36px;padding:0 13px;border:0;border-radius:10px;background:${primary};color:#fff;font:800 11px inherit;cursor:pointer}.cs-csat-thanks{display:grid;gap:5px;margin-top:14px;padding:14px;border:1px solid ${line};border-radius:14px;background:${surface};text-align:center}.cs-csat-thanks strong{font-size:18px;letter-spacing:2px;color:${primary}}.cs-kb{display:grid;gap:10px;margin-bottom:16px}.cs-kb-head{display:flex;align-items:center;justify-content:space-between;gap:8px}.cs-kb-head strong{font-size:13px}.cs-kb-search{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:7px}.cs-kb-search input{min-width:0;min-height:40px;padding:9px 10px;border:1px solid ${line};border-radius:10px;background:${surface};color:${text};font:inherit}.cs-kb-search button,.cs-kb-back{min-height:40px;padding:0 12px;border:1px solid ${line};border-radius:10px;background:${surface};color:${text};font:800 11px inherit;cursor:pointer}.cs-kb-categories{display:flex;gap:6px;overflow-x:auto;padding-bottom:2px}.cs-kb-category{flex:0 0 auto;padding:6px 9px;border:1px solid ${line};border-radius:999px;background:${surface};color:${text};font:700 10px inherit;cursor:pointer}.cs-kb-list{display:grid;gap:7px}.cs-kb-item{display:grid;gap:3px;width:100%;padding:10px;border:1px solid ${line};border-radius:11px;background:${surface};color:${text};text-align:left;cursor:pointer}.cs-kb-item strong{font-size:12px}.cs-kb-item span{color:${muted};font-size:10px;line-height:1.4}.cs-kb-article{display:grid;gap:10px;padding:12px;border:1px solid ${line};border-radius:13px;background:${surface}}.cs-kb-article h2{margin:0;font-size:15px;line-height:1.35}.cs-kb-article p{margin:0;color:${text};font-size:12px;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere}.cs-kb-article small{color:${muted};font-size:10px}.cs-kb-feedback{display:flex;align-items:center;gap:7px;padding-top:8px;border-top:1px solid ${line}}.cs-kb-feedback span{margin-right:auto;color:${muted};font-size:10px}.cs-kb-feedback button{width:34px;height:32px;border:1px solid ${line};border-radius:9px;background:${canvas};color:${text};cursor:pointer}.cs-kb-divider{display:flex;align-items:center;gap:8px;margin:14px 0;color:${muted};font-size:10px}.cs-kb-divider:before,.cs-kb-divider:after{content:"";height:1px;flex:1;background:${line}}.cs-privacy-actions{display:flex;align-items:center;justify-content:center;margin-top:14px;padding-top:12px;border-top:1px solid ${line}}.cs-privacy-delete{border:0;background:transparent;color:${muted};font:700 10px inherit;text-decoration:underline;cursor:pointer}.cs-privacy-delete:disabled{opacity:.55;cursor:default}.cs-privacy-confirmed{margin:0 0 12px;padding:10px 12px;border:1px solid ${line};border-radius:10px;background:${surface};color:${muted};font-size:11px;line-height:1.45}.cs-call-incoming{display:grid;justify-items:center;gap:9px;padding:22px 14px;border:1px solid ${line};border-radius:16px;background:${surface};text-align:center}.cs-call-incoming small{color:${muted}}.cs-call-avatar{display:grid;place-items:center;width:72px;height:72px;border-radius:50%;background:${primary};color:#fff;font-size:24px;font-weight:850}.cs-call-actions{display:flex;gap:9px;margin-top:5px}.cs-call-actions button,.cs-call-controls button{min-height:40px;padding:0 15px;border:0;border-radius:999px;color:#fff;font:800 11px inherit;cursor:pointer}.cs-call-decline,.cs-call-end{background:#c62828}.cs-call-accept{background:#198754}.cs-call-stage{position:relative;min-height:360px;height:100%;overflow:hidden;border-radius:14px;background:#090909;color:#fff}.cs-call-remote{width:100%;height:100%;object-fit:cover;background:#090909}.cs-call-local{position:absolute;right:10px;bottom:66px;width:30%;aspect-ratio:3/4;object-fit:cover;border:1px solid rgba(255,255,255,.3);border-radius:10px;background:#1c1c1c}.cs-call-audio{height:100%;display:grid;place-content:center;justify-items:center;gap:8px;background:radial-gradient(circle,#292929,#080808 72%)}.cs-call-audio small{color:rgba(255,255,255,.65)}.cs-call-controls{position:absolute;left:0;right:0;bottom:0;display:flex;justify-content:center;gap:7px;padding:12px;background:rgba(0,0,0,.62)}.cs-call-controls button{background:rgba(255,255,255,.16)}.cs-call-controls .cs-call-end{background:#c62828}@media(max-width:520px){.cs-wrap{left:12px;right:12px;bottom:max(12px,env(safe-area-inset-bottom));justify-items:stretch}.cs-panel{width:100%;height:min(72dvh,620px);border-radius:18px}.cs-launcher{justify-self:${side === "left" ? "start" : "end"}}}`;
  }

  function messengerStyles() {
    return `
      @keyframes cs-panel-in{from{opacity:0;transform:translateY(12px) scale(.975)}to{opacity:1;transform:none}}
      @keyframes cs-panel-out{from{opacity:1;transform:none}to{opacity:0;transform:translateY(8px) scale(.985)}}
      .cs-panel{position:relative;width:min(400px,calc(100vw - 24px));height:min(650px,calc(100dvh - 88px));animation:cs-panel-in 160ms cubic-bezier(.2,.8,.2,1);transform-origin:bottom right}
      .cs-panel.is-closing{animation:cs-panel-out 150ms ease forwards;pointer-events:none}
      .cs-header{min-height:72px;padding:10px 12px;gap:10px}
      .cs-back,.cs-header-call{display:grid;place-items:center;width:38px;height:38px;flex:0 0 auto;border:0;border-radius:50%;background:transparent;color:${state.config && state.config.text_color ? state.config.text_color : "#111"};font:800 21px/1 inherit;cursor:pointer}
      .cs-back:hover,.cs-header-call:hover{background:rgba(127,127,127,.12)}
      .cs-back svg{display:none}.cs-back:before{content:"";width:10px;height:10px;margin-left:4px;border-left:2px solid currentColor;border-bottom:2px solid currentColor;transform:rotate(45deg)}
      .cs-mark{width:44px;height:44px;border-radius:50%;font-size:16px}
      .cs-title strong{font-size:16px;line-height:1.3}.cs-title small{font-size:12px;line-height:1.35}
      .cs-header-actions{display:flex;align-items:center;gap:2px;margin-left:auto}
      .cs-header-call:disabled{opacity:.35;cursor:default}
      .cs-body{padding:18px 12px 12px;scroll-behavior:smooth;background-color:${state.config && state.config.theme === "dark" ? "#101010" : "#fafafa"};background-image:radial-gradient(rgba(0,0,0,.022) .7px,transparent .7px);background-size:14px 14px}
      .cs-body{scrollbar-width:none}.cs-body::-webkit-scrollbar{width:0;height:0}
      .cs-error{position:sticky;z-index:3;top:0;display:flex;align-items:center;gap:10px;margin:0 2px 12px;padding:9px 10px;border-color:#efb2b2;border-radius:12px;box-shadow:0 6px 22px rgba(141,27,27,.08)}
      .cs-error span{min-width:0;flex:1}.cs-error button{width:26px;height:26px;border:0;border-radius:50%;background:rgba(141,27,27,.08);color:inherit;font:700 17px/1 inherit;cursor:pointer}
      .cs-messages{gap:3px;min-height:100%}
      .cs-message{max-width:86%;gap:2px;margin-top:9px}
      .cs-message.is-grouped{margin-top:0}
      .cs-bubble{padding:10px 12px 8px;border-color:#dadadd;border-radius:18px;background:${state.config && state.config.theme === "dark" ? "#202020" : "#fff"};color:${state.config && state.config.theme === "dark" ? "#f5f5f5" : "#141414"};font-size:15px;line-height:1.35;box-shadow:0 1px 1px rgba(0,0,0,.025)}
      .cs-message.team .cs-bubble{border-bottom-left-radius:5px}
      .cs-message.team.is-grouped .cs-bubble{border-top-left-radius:5px}
      .cs-message.visitor .cs-bubble{border-color:#d1d1d4;border-bottom-right-radius:5px;background:${state.config && state.config.theme === "dark" ? "#272727" : "#f2f2f3"};color:${state.config && state.config.theme === "dark" ? "#f5f5f5" : "#141414"}}
      .cs-message.visitor.is-grouped .cs-bubble{border-top-right-radius:5px}
      .cs-bubble-text{display:inline}
      .cs-meta{display:inline-block;margin:0 0 0 7px;color:#8a8a8f;vertical-align:baseline;text-align:right;white-space:nowrap;opacity:1}
      .cs-message.visitor .cs-meta{color:#77777c}
      .cs-receipt{font-weight:800;letter-spacing:-2px;color:#262626}.cs-receipt.is-pending{font-weight:500;letter-spacing:0;color:#8a8a8f}.cs-receipt.is-failed{font-weight:700;letter-spacing:0;color:#c62828}
      .cs-composer{grid-template-columns:auto minmax(0,1fr) auto;align-items:end;padding:10px 12px;gap:8px}
      .cs-composer textarea{min-height:46px;max-height:108px;padding:12px 15px;border-radius:24px;line-height:20px;overflow-y:auto}
      .cs-tool,.cs-send{width:46px;height:46px;min-width:46px;padding:0;border-radius:50%}
      .cs-tool.is-voice,.cs-send{border-color:${state.config ? state.config.primary_color : "#111"};background:${state.config ? state.config.primary_color : "#111"};color:#fff}
      .cs-tool[hidden],.cs-send[hidden],.cs-jump[hidden]{display:none!important}
      .cs-visually-hidden{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;white-space:nowrap!important;border:0!important}
      .cs-back svg,.cs-header-call svg,.cs-tool svg,.cs-send svg{width:21px;height:21px;fill:none;stroke:currentColor;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round}
      .cs-send{font-size:0}.cs-send svg{width:18px;height:18px;fill:currentColor;stroke:none}
      .cs-send:disabled{opacity:.38;cursor:default}
      .cs-jump{position:absolute;z-index:4;right:18px;bottom:78px;width:48px;height:48px;display:grid;place-items:center;border:1px solid #d7d7da;border-radius:50%;background:${state.config && state.config.theme === "dark" ? "#202020" : "#fff"};color:${state.config && state.config.theme === "dark" ? "#fff" : "#111"};box-shadow:0 5px 18px rgba(0,0,0,.11);cursor:pointer}
      .cs-jump svg{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
      .cs-launcher{animation:cs-panel-in 160ms ease}
      @media(max-width:520px){
        .cs-panel{position:fixed;inset:0;width:100vw;height:100dvh;border:0;border-radius:0;box-shadow:none;transform-origin:bottom center}
        .cs-wrap{left:8px;right:8px;bottom:max(8px,env(safe-area-inset-bottom))}
        .cs-header{min-height:70px;padding-top:max(10px,env(safe-area-inset-top))}
        .cs-body{padding-top:14px}
        .cs-composer{padding-bottom:max(10px,env(safe-area-inset-bottom))}
      }
      @media(prefers-reduced-motion:reduce){.cs-panel,.cs-panel.is-closing,.cs-launcher{animation:none}}
    `;
  }

  function node(tag, className, text) {
    var element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function buttonIcon(button, kind) {
    var icons = {
      back: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 18-6-6 6-6"/></svg>',
      audio: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.6 3.8 9 3.2l2.1 5-1.7 1.4a15 15 0 0 0 5 5l1.4-1.7 5 2.1-.6 2.4c-.3 1.2-1.4 2-2.6 1.9A15.8 15.8 0 0 1 4.7 6.4c-.1-1.2.7-2.3 1.9-2.6Z"/></svg>',
      video: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="6" width="13" height="12" rx="2"/><path d="m16 10 5-3v10l-5-3"/></svg>',
      attach: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8.5 12.5 6.7-6.7a3.2 3.2 0 0 1 4.5 4.5l-9 9a5 5 0 0 1-7.1-7.1l8.4-8.4"/><path d="m6.4 14.3 7.7-7.7"/></svg>',
      mic: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3"/></svg>',
      stop: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="1"/></svg>',
      send: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m3 20 18-8L3 4l3 7 9 1-9 1-3 7Z"/></svg>',
      jump: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 8 6 6 6-6"/><path d="m6 13 6 6 6-6"/></svg>',
    };
    button.innerHTML = icons[kind] || "";
    return button;
  }

  function scrollMessages() {
    if (!shadow) return;
    state.followLatest = true;
    var body = shadow.querySelector(".cs-body");
    var jump = shadow.querySelector(".cs-jump");
    if (jump) jump.hidden = true;
    if (body) window.requestAnimationFrame(function () { body.scrollTop = body.scrollHeight; });
  }

  function renderAttachment(container, attachment) {
    var media = node("div", "cs-media");
    var previewUrl = attachment.preview_url;
    if (previewUrl && attachment.can_preview_inline) {
      var element = null;
      if (attachment.media_kind === "image") { element = node("img"); element.alt = attachment.original_name || "Image"; }
      else if (attachment.media_kind === "video") { element = node("video"); element.controls = true; element.playsInline = true; }
      else if (attachment.media_kind === "audio") { element = node("audio"); element.controls = true; }
      if (element) {
        media.appendChild(element);
        authorizedBlob(previewUrl).then(function (blob) {
          var objectUrl = URL.createObjectURL(blob); state.objectUrls.push(objectUrl); element.src = objectUrl;
        }).catch(function () {});
      }
    }
    if (attachment.media_kind === "file" || !attachment.can_preview_inline) {
      var card = node("div", "cs-file"); card.appendChild(node("strong", "", "↧")); card.appendChild(node("span", "", attachment.original_name || "File")); media.appendChild(card);
    }
    var download = node("button", "cs-download", "Download"); download.type = "button";
    download.onclick = function () {
      authorizedBlob(attachment.download_url).then(function (blob) {
        var url = URL.createObjectURL(blob); var anchor = document.createElement("a"); anchor.href = url; anchor.download = attachment.original_name || "download"; document.body.appendChild(anchor); anchor.click(); anchor.remove(); window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      }).catch(function () { state.error = "Download failed."; render(); });
    };
    media.appendChild(download); container.appendChild(media);
  }

  function renderMessages(body) {
    var list = node("div", "cs-messages");
    if (!state.messages.length) list.appendChild(node("div", "cs-empty", "Send a message to start the conversation."));
    var previousSender = "";
    state.messages.forEach(function (message) {
      var visitor = message.sender && message.sender.kind === "visitor";
      var sender = visitor ? "visitor" : "team";
      var row = node("div", "cs-message " + sender + (previousSender === sender ? " is-grouped" : ""));
      previousSender = sender;
      var createdAt = message.created_at ? new Date(message.created_at) : null;
      var timeLabel = createdAt && !isNaN(createdAt.getTime()) ? createdAt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";
      var receiptStatus = visitor ? String(message.receipt_status || message.delivery_status || "sent").toLowerCase() : "";
      var receiptLabel = receiptStatus === "pending" ? "Sending" : receiptStatus === "failed" ? "Failed" : receiptStatus === "sent" ? "\u2713" : receiptStatus ? "\u2713\u2713" : "";
      var bubble = node("div", "cs-bubble");
      bubble.appendChild(node("div", "cs-bubble-text", message.text || ""));
      (message.attachments || []).forEach(function (attachment) { renderAttachment(bubble, attachment); });
      var meta = node("span", "cs-meta", timeLabel);
      if (visitor && receiptLabel) {
        meta.appendChild(document.createTextNode(" "));
        meta.appendChild(node("span", "cs-receipt is-" + receiptStatus, receiptLabel));
      }
      bubble.appendChild(meta);
      row.appendChild(bubble);
      list.appendChild(row);
    });
    if (state.teamTyping) list.appendChild(node("div", "cs-typing", "Support team is typing…"));
    body.appendChild(list);
  }

  function renderCSAT(body) {
    var payload = state.csat;
    var survey = payload && payload.survey;
    if (!payload || !payload.enabled || !survey) return;
    if (survey.status === "submitted") {
      var thanks = node("div", "cs-csat-thanks");
      thanks.appendChild(node("strong", "", "★★★★★".slice(0, survey.rating || 0) + "☆☆☆☆☆".slice(0, 5 - (survey.rating || 0))));
      thanks.appendChild(node("span", "", "Thank you for your feedback."));
      body.appendChild(thanks); return;
    }
    if (survey.status !== "pending" || !survey.available) return;
    var card = node("div", "cs-csat");
    card.appendChild(node("strong", "", "How was your support experience?"));
    card.appendChild(node("p", "", "Choose a rating from 1 to 5."));
    var stars = node("div", "cs-csat-stars");
    for (var rating = 1; rating <= 5; rating += 1) {
      (function (value) {
        var star = node("button", "cs-csat-star" + (state.csatRating >= value ? " is-selected" : ""), "★");
        star.type = "button"; star.setAttribute("aria-label", value + " out of 5");
        star.onclick = function () { state.csatRating = value; render(); }; stars.appendChild(star);
      })(rating);
    }
    card.appendChild(stars);
    if (payload.allow_comment) {
      var comment = node("textarea"); comment.placeholder = "Optional comment"; comment.maxLength = 2000; comment.value = state.csatComment;
      comment.oninput = function () { state.csatComment = comment.value; }; card.appendChild(comment);
    }
    var actions = node("div", "cs-csat-actions");
    var later = node("button", "cs-csat-secondary", "Not now"); later.type = "button"; later.onclick = function () { dismissCSAT().catch(function () {}); }; actions.appendChild(later);
    var submit = node("button", "cs-csat-submit", state.csatSubmitting ? "Sending…" : "Send rating"); submit.type = "button"; submit.disabled = state.csatSubmitting || !state.csatRating;
    submit.onclick = function () { if (state.csatRating) submitCSAT(state.csatRating, state.csatComment).catch(function () {}); }; actions.appendChild(submit);
    card.appendChild(actions); body.appendChild(card);
  }

  function renderKnowledge(body) {
    if (!state.config.knowledge_enabled || !state.knowledge || !state.knowledge.enabled) return;
    var section = node("section", "cs-kb");
    if (state.selectedArticle) {
      var back = node("button", "cs-kb-back", "← All answers"); back.type = "button"; back.onclick = function () { state.selectedArticle = null; render(); }; section.appendChild(back);
      var article = node("article", "cs-kb-article"); article.appendChild(node("small", "", state.selectedArticle.category ? state.selectedArticle.category.name : "Help article")); article.appendChild(node("h2", "", state.selectedArticle.title)); article.appendChild(node("p", "", state.selectedArticle.body || ""));
      if (state.knowledge.allow_feedback) {
        var feedback = node("div", "cs-kb-feedback"); feedback.appendChild(node("span", "", state.selectedArticle.__feedback ? "Thanks for your feedback." : "Was this helpful?"));
        var yes = node("button", "", "Yes"); yes.type = "button"; yes.disabled = Boolean(state.selectedArticle.__feedback); yes.onclick = function () { submitKnowledgeFeedback(state.selectedArticle.id, true).catch(function () {}); }; feedback.appendChild(yes);
        var no = node("button", "", "No"); no.type = "button"; no.disabled = Boolean(state.selectedArticle.__feedback); no.onclick = function () { submitKnowledgeFeedback(state.selectedArticle.id, false).catch(function () {}); }; feedback.appendChild(no); article.appendChild(feedback);
      }
      section.appendChild(article); body.appendChild(section); return;
    }
    var head = node("div", "cs-kb-head"); head.appendChild(node("strong", "", "Find an answer")); if (state.knowledgeLoading) head.appendChild(node("small", "", "Searching…")); section.appendChild(head);
    var form = node("form", "cs-kb-search"); var input = node("input"); input.type = "search"; input.placeholder = "Search help articles"; input.value = state.knowledgeQuery; input.oninput = function () { state.knowledgeQuery = input.value; }; var search = node("button", "", "Search"); search.type = "submit"; form.appendChild(input); form.appendChild(search); form.onsubmit = function (event) { event.preventDefault(); loadKnowledge(state.knowledgeQuery, ""); }; section.appendChild(form);
    if (state.knowledge.categories && state.knowledge.categories.length) { var categories = node("div", "cs-kb-categories"); state.knowledge.categories.forEach(function (category) { var button = node("button", "cs-kb-category", category.name); button.type = "button"; button.onclick = function () { loadKnowledge("", category.id); }; categories.appendChild(button); }); section.appendChild(categories); }
    var list = node("div", "cs-kb-list"); if (!state.knowledge.articles || !state.knowledge.articles.length) list.appendChild(node("div", "cs-empty", state.knowledgeLoading ? "Searching answers…" : "No matching answers."));
    (state.knowledge.articles || []).forEach(function (article) { var item = node("button", "cs-kb-item"); item.type = "button"; item.appendChild(node("strong", "", article.title)); item.appendChild(node("span", "", article.summary || String(article.body || "").slice(0, 120))); item.onclick = function () { loadKnowledgeArticle(article.id).catch(function () {}); }; list.appendChild(item); }); section.appendChild(list); body.appendChild(section);
  }

  function renderStart(body) {
    if (state.deletionRequested) body.appendChild(node("p", "cs-privacy-confirmed", "Your Support data deletion request was received."));
    body.appendChild(node("p", "cs-welcome", state.config.welcome_text || "Hi, how can we help?"));
    renderKnowledge(body);
    if (state.config.knowledge_enabled) body.appendChild(node("div", "cs-kb-divider", "Still need help?"));
    var form = node("form", "cs-form");
    var name = node("input", "cs-field"); name.name = "name"; name.placeholder = "Your name"; name.autocomplete = "name";
    var email = node("input", "cs-field"); email.name = "email"; email.type = "email"; email.placeholder = "Email address"; email.autocomplete = "email";
    if (state.config.require_name) form.appendChild(name);
    if (state.config.require_email) form.appendChild(email);
    var button = node("button", "cs-primary", state.loading ? "Starting…" : "Start chat"); button.type = "submit"; button.disabled = state.loading;
    form.appendChild(button);
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      state.loading = true; state.error = ""; render();
      api.createSession({ name: name.value || "", email: email.value || "" })
        .then(function () { state.loading = false; render(); startPolling(); })
        .catch(function (error) { state.loading = false; state.error = error.message; render(); });
    });
    body.appendChild(form);
    if (state.config.privacy_note) body.appendChild(node("p", "cs-note", state.config.privacy_note));
  }

  function render() {
    if (!shadow || !state.config) return;
    var previousBody = shadow.querySelector(".cs-body");
    var distanceFromBottom = previousBody ? Math.max(0, previousBody.scrollHeight - previousBody.scrollTop - previousBody.clientHeight) : 0;
    var followLatest = state.followLatest !== false;
    clearObjectUrls();
    shadow.innerHTML = "";
    var style = node("style"); style.textContent = styles() + messengerStyles(); shadow.appendChild(style);
    var wrap = node("div", "cs-wrap");
    var panel = node("section", "cs-panel" + (state.closing ? " is-closing" : "")); panel.setAttribute("aria-label", state.config.brand_name || "Support Chat");
    var header = node("header", "cs-header");
    var back = buttonIcon(node("button", "cs-back"), "back"); back.type = "button"; back.setAttribute("aria-label", "Close support chat"); back.disabled = Boolean(state.call && !callTerminal(state.call)); back.onclick = api.close; header.appendChild(back);
    header.appendChild(node("span", "cs-mark", (state.config.brand_name || "S").slice(0, 1).toUpperCase()));
    var title = node("span", "cs-title"); title.appendChild(node("strong", "", state.config.brand_name)); title.appendChild(node("small", "", state.session ? (state.socketState === "open" ? "Online · Support" : "Connecting…") : state.config.website_name)); header.appendChild(title);
    if (state.session && state.config.calls_enabled) {
      var headerActions = node("span", "cs-header-actions");
      if (state.config.allow_audio_calls) {
        var audioCall = buttonIcon(node("button", "cs-header-call"), "audio"); audioCall.type = "button"; audioCall.title = "Start audio call"; audioCall.setAttribute("aria-label", "Start audio call"); audioCall.disabled = state.callStarting || Boolean(state.call && !callTerminal(state.call)); audioCall.onclick = function () { startVisitorCall("voice"); }; headerActions.appendChild(audioCall);
      }
      if (state.config.allow_video_calls) {
        var videoCall = buttonIcon(node("button", "cs-header-call"), "video"); videoCall.type = "button"; videoCall.title = "Start video call"; videoCall.setAttribute("aria-label", "Start video call"); videoCall.disabled = state.callStarting || Boolean(state.call && !callTerminal(state.call)); videoCall.onclick = function () { startVisitorCall("video"); }; headerActions.appendChild(videoCall);
      }
      header.appendChild(headerActions);
    }
    panel.appendChild(header);
    var body = node("div", "cs-body");
    if (state.error) {
      var errorBanner = node("div", "cs-error");
      errorBanner.appendChild(node("span", "", state.error));
      var dismissError = node("button", "", "\u00d7"); dismissError.type = "button"; dismissError.setAttribute("aria-label", "Dismiss error");
      dismissError.onclick = function () { state.error = ""; render(); };
      errorBanner.appendChild(dismissError); body.appendChild(errorBanner);
    }
    if (state.session) {
      var callRendered = renderCall(body);
      if (!callRendered) { renderMessages(body); renderCSAT(body); }
    } else renderStart(body);
    panel.appendChild(body);
    var composer = node("form", "cs-composer"); if (state.call && !callTerminal(state.call)) composer.style.display = "none";
    if (state.pendingUploads.length) {
      var uploadList = node("div", "cs-upload-list");
      state.pendingUploads.forEach(function (upload, index) {
        var chip = node("span", "cs-upload-chip"); chip.appendChild(node("span", "", upload.name));
        var remove = node("button", "", "×"); remove.type = "button"; remove.onclick = function () { state.pendingUploads.splice(index, 1); render(); }; chip.appendChild(remove); uploadList.appendChild(chip);
      });
      composer.appendChild(uploadList);
    }
    var fileInput = node("input", "cs-hidden"); fileInput.type = "file"; fileInput.multiple = true; fileInput.disabled = state.loading || state.uploading;
    fileInput.onchange = function () { addPendingFiles(fileInput.files); };
    var attach = buttonIcon(node("button", "cs-tool"), "attach"); attach.type = "button"; attach.title = "Attach files"; attach.setAttribute("aria-label", "Attach files"); attach.disabled = state.loading || state.uploading || !state.config.allow_attachments; attach.onclick = function () { fileInput.click(); };
    var textarea = node("textarea"); textarea.placeholder = "Write a message…"; textarea.rows = 1; textarea.value = state.draft; textarea.disabled = state.loading || state.uploading;
    var voice = buttonIcon(node("button", "cs-tool is-voice" + (state.recording ? " recording" : "")), state.recording ? "stop" : "mic"); voice.type = "button"; voice.title = state.recording ? "Send voice message" : "Record voice message"; voice.setAttribute("aria-label", voice.title); voice.disabled = state.loading || state.uploading || !state.config.allow_attachments; voice.onclick = toggleVoiceRecording;
    var send = buttonIcon(node("button", "cs-send"), "send"); send.type = "submit"; send.setAttribute("aria-label", "Send"); send.disabled = state.loading || state.uploading || (!state.draft.trim() && !state.pendingUploads.length);
    function updateComposerAction() {
      var hasMessage = Boolean(state.draft.trim() || state.pendingUploads.length);
      voice.hidden = hasMessage && !state.recording;
      send.hidden = !hasMessage || state.recording;
      send.disabled = state.loading || state.uploading || !hasMessage;
    }
    updateComposerAction();
    textarea.oninput = function () {
      state.draft = textarea.value;
      textarea.style.height = "auto";
      textarea.style.height = Math.min(108, textarea.scrollHeight) + "px";
      updateComposerAction();
      reportVisitorTyping(Boolean(state.draft.trim()));
    };
    textarea.onblur = function () { reportVisitorTyping(false); };
    textarea.onkeydown = function (event) {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        if (!send.disabled) composer.requestSubmit();
      }
    };
    composer.appendChild(fileInput); composer.appendChild(attach); composer.appendChild(textarea); composer.appendChild(voice); composer.appendChild(send);
    composer.addEventListener("submit", function (event) {
      event.preventDefault();
      var text = state.draft.trim(); var attachmentIds = state.pendingUploads.map(function (upload) { return upload.id; }); if (!text && !attachmentIds.length) return;
      reportVisitorTyping(false);
      state.loading = true; state.error = ""; render();
      sendMessage(text, attachmentIds, false).then(function () { state.loading = false; render(); scrollMessages(); }).catch(function (error) { state.loading = false; state.error = error.message; render(); });
    });
    panel.appendChild(composer);
    if (state.session && state.config.visitor_deletion_enabled) {
      var privacyDelete = node("button", "cs-visually-hidden", "Delete my support data");
      privacyDelete.type = "button"; privacyDelete.disabled = state.deletionSubmitting;
      privacyDelete.onclick = function () {
        if (window.confirm("Permanently delete this Support conversation and visitor data?")) requestVisitorDeletion().catch(function () {});
      };
      panel.appendChild(privacyDelete);
    }
    if (state.session && !(state.call && !callTerminal(state.call))) {
      var jump = buttonIcon(node("button", "cs-jump"), "jump"); jump.type = "button"; jump.hidden = true; jump.setAttribute("aria-label", "Jump to latest message"); jump.onclick = scrollMessages;
      body.onscroll = function () {
        state.followLatest = body.scrollHeight - body.scrollTop - body.clientHeight < 96;
        jump.hidden = state.followLatest;
      };
      panel.appendChild(jump);
    }
    wrap.appendChild(panel);
    if (!state.open) {
      var launcher = node("button", "cs-launcher", state.config.launcher_text || "Chat"); launcher.type = "button"; launcher.onclick = api.open; if (state.hasUnread) launcher.appendChild(node("span", "cs-unread", "1")); wrap.appendChild(launcher);
    }
    shadow.appendChild(wrap);
    if (state.open && state.session && !(state.call && !callTerminal(state.call))) {
      window.requestAnimationFrame(function () {
        var nextBody = shadow && shadow.querySelector(".cs-body");
        var nextJump = shadow && shadow.querySelector(".cs-jump");
        if (!nextBody) return;
        nextBody.scrollTop = followLatest
          ? nextBody.scrollHeight
          : Math.max(0, nextBody.scrollHeight - nextBody.clientHeight - distanceFromBottom);
        if (nextJump) nextJump.hidden = nextBody.scrollHeight - nextBody.scrollTop - nextBody.clientHeight < 96;
      });
    }
  }

  host = document.createElement("div");
  host.id = "crescentsupport-widget-" + siteKey;
  shadow = host.attachShadow ? host.attachShadow({ mode: "open" }) : host;
  document.body.appendChild(host);
  window.CrescentSupportChat = api;

  ["pushState", "replaceState"].forEach(function (method) {
    var original = window.history && window.history[method];
    if (typeof original !== "function" || original.__crescentSupportWrapped) return;
    var wrapped = function () {
      var result = original.apply(this, arguments);
      window.setTimeout(function () { reportVisitorActivity(true); }, 0);
      return result;
    };
    wrapped.__crescentSupportWrapped = true;
    window.history[method] = wrapped;
  });
  window.addEventListener("popstate", function () { reportVisitorActivity(true); });
  window.addEventListener("hashchange", function () { reportVisitorActivity(true); });
  window.addEventListener("focus", function () { reportVisitorActivity(true); acknowledgeLatestTeamMessage(); });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") { reportVisitorActivity(true); acknowledgeLatestTeamMessage(); }
  });

  api.ready = request("/config/", { method: "GET" }).then(function (config) {
    state.config = config;
    return Promise.all([resumeSession(), config.knowledge_enabled ? loadKnowledge("", "") : Promise.resolve(null)]).then(function () {
      render();
      if (state.session) { loadMessages().catch(function () {}); loadCSAT().catch(function () {}); loadActiveCall().catch(function () {}); }
      window.dispatchEvent(new CustomEvent("crescentsupport:ready", { detail: { api: api, config: config } }));
      return api;
    });
  }).catch(function () { if (host && host.parentNode) host.parentNode.removeChild(host); return null; });
})();
