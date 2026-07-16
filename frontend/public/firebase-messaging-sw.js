/* global importScripts, firebase */

(() => {
  const params = new URL(self.location.href).searchParams;
  const config = {
    apiKey: params.get("apiKey") || "",
    authDomain: params.get("authDomain") || undefined,
    projectId: params.get("projectId") || "",
    storageBucket: params.get("storageBucket") || undefined,
    messagingSenderId: params.get("messagingSenderId") || "",
    appId: params.get("appId") || "",
  };

  if (!config.apiKey || !config.projectId || !config.messagingSenderId || !config.appId) return;

  importScripts("https://www.gstatic.com/firebasejs/10.13.2/firebase-app-compat.js");
  importScripts("https://www.gstatic.com/firebasejs/10.13.2/firebase-messaging-compat.js");

  if (!firebase.apps.length) {
    firebase.initializeApp(config);
  }

  const messaging = firebase.messaging();

  messaging.onBackgroundMessage((payload) => {
    const title = payload?.notification?.title || payload?.data?.title || payload?.data?.sender_name || "New message";
    const isMessageNotification = Boolean(payload?.data?.conversation_id && payload?.data?.message_id && !payload?.data?.call_id);
    const options = {
      body: payload?.notification?.body || payload?.data?.body || "Open the chat to view it.",
      data: payload?.data || {},
      tag: payload?.data?.conversation_id ? `message:${payload.data.conversation_id}` : undefined,
      actions: isMessageNotification ? [{ action: "reply", title: "Reply" }] : [],
    };
    self.registration.showNotification(title, options);
  });

  self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const conversationId = event.notification?.data?.conversation_id;
    const callId = event.notification?.data?.call_id;
    const replyRequested = event.action === "reply";
    const targetPath = callId
      ? `/calls/${callId}`
      : conversationId ? `/chat/${conversationId}${replyRequested ? "?reply=1" : ""}` : "/";
    event.waitUntil((async () => {
      const clientList = await clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of clientList) {
        const clientUrl = new URL(client.url);
        if (clientUrl.origin !== self.location.origin) continue;
        await client.focus();
        if ("navigate" in client) {
          await client.navigate(targetPath);
        }
        return;
      }
      await clients.openWindow(targetPath);
    })());
  });
})();
