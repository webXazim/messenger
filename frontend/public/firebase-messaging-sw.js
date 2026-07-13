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
    const title = payload?.notification?.title || "New message";
    const options = {
      body: payload?.notification?.body || "Open the chat to view it.",
      data: payload?.data || {},
    };
    self.registration.showNotification(title, options);
  });

  self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const conversationId = event.notification?.data?.conversation_id;
    const callId = event.notification?.data?.call_id;
    const targetPath = conversationId ? `/chat/${conversationId}` : callId ? `/calls/${callId}` : "/";
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
