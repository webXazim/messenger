const WEB_PUSH_TOKEN_STORAGE_KEY = "messenger:web-push-token";
const WEB_PUSH_LAST_PROMPT_KEY = "messenger:web-push-last-prompt";
const FIREBASE_SCRIPT_BASE = "https://www.gstatic.com/firebasejs/10.13.2";

type FirebaseBrowserConfig = {
  apiKey: string;
  authDomain?: string;
  projectId: string;
  storageBucket?: string;
  messagingSenderId: string;
  appId: string;
};

type FirebaseWindow = Window & {
  firebase?: {
    apps?: Array<{ name?: string }>;
    initializeApp: (config: FirebaseBrowserConfig) => unknown;
    app: () => unknown;
    messaging: () => {
      getToken: (options: { vapidKey: string; serviceWorkerRegistration: ServiceWorkerRegistration }) => Promise<string>;
    };
  };
};

function getFirebaseConfig(): FirebaseBrowserConfig | null {
  const apiKey = import.meta.env.VITE_FIREBASE_API_KEY?.trim();
  const projectId = import.meta.env.VITE_FIREBASE_PROJECT_ID?.trim();
  const messagingSenderId = import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID?.trim();
  const appId = import.meta.env.VITE_FIREBASE_APP_ID?.trim();
  if (!apiKey || !projectId || !messagingSenderId || !appId) return null;
  return {
    apiKey,
    authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN?.trim(),
    projectId,
    storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET?.trim(),
    messagingSenderId,
    appId,
  };
}

function getVapidKey() {
  return import.meta.env.VITE_FIREBASE_VAPID_KEY?.trim() || "";
}

function getServiceWorkerUrl(config: FirebaseBrowserConfig) {
  const params = new URLSearchParams();
  Object.entries(config).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  return `/firebase-messaging-sw.js?${params.toString()}`;
}

function loadScript(src: string) {
  return new Promise<void>((resolve, reject) => {
    const existing = document.querySelector(`script[data-runtime-src="${src}"]`) as HTMLScriptElement | null;
    if (existing) {
      if (existing.dataset.loaded === "true") resolve();
      else existing.addEventListener("load", () => resolve(), { once: true });
      return;
    }
    const script = document.createElement("script");
    script.src = src;
    script.async = true;
    script.dataset.runtimeSrc = src;
    script.addEventListener("load", () => {
      script.dataset.loaded = "true";
      resolve();
    }, { once: true });
    script.addEventListener("error", () => reject(new Error(`Unable to load ${src}`)), { once: true });
    document.head.appendChild(script);
  });
}

async function ensureFirebaseMessaging() {
  const win = window as FirebaseWindow;
  await loadScript(`${FIREBASE_SCRIPT_BASE}/firebase-app-compat.js`);
  await loadScript(`${FIREBASE_SCRIPT_BASE}/firebase-messaging-compat.js`);
  if (!win.firebase?.messaging) throw new Error("Browser notifications are unavailable in this browser.");
  const config = getFirebaseConfig();
  if (!config) throw new Error("Browser notification configuration is incomplete.");
  if (!win.firebase.apps?.length) {
    win.firebase.initializeApp(config);
  }
  return { firebase: win.firebase, config };
}

export function getStoredWebPushToken() {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(WEB_PUSH_TOKEN_STORAGE_KEY) || "";
}

export function clearStoredWebPushToken() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(WEB_PUSH_TOKEN_STORAGE_KEY);
}

export function rememberWebPushPrompt() {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(WEB_PUSH_LAST_PROMPT_KEY, String(Date.now()));
}

export function getLastWebPushPromptAt() {
  if (typeof window === "undefined") return 0;
  return Number(window.localStorage.getItem(WEB_PUSH_LAST_PROMPT_KEY) || 0);
}

export function isWebPushSupported() {
  if (typeof window === "undefined") return false;
  return window.isSecureContext && "Notification" in window && "serviceWorker" in navigator && "PushManager" in window;
}

export async function getWebPushStatus() {
  return {
    supported: isWebPushSupported(),
    permission: typeof window !== "undefined" && "Notification" in window ? Notification.permission : "default",
    configured: Boolean(getFirebaseConfig() && getVapidKey()),
    token: getStoredWebPushToken(),
  };
}

export function getWebPushPermissionMessage(permission: NotificationPermission) {
  if (permission === "granted") return "Notifications are enabled on this browser.";
  if (permission === "denied") return "Notifications are blocked in this browser. Enable them in browser site settings to restore chat alerts.";
  return "Notifications are not enabled yet on this browser.";
}

export async function registerBrowserForWebPush(options: { interactive?: boolean } = {}) {
  if (!isWebPushSupported()) throw new Error("Web push is not supported in this browser.");
  const interactive = options.interactive !== false;
  const { firebase, config } = await ensureFirebaseMessaging();
  const vapidKey = getVapidKey();
  if (!vapidKey) throw new Error("Browser notification configuration is incomplete.");
  let permission = Notification.permission;
  if (permission === "default" && interactive) {
    permission = await Notification.requestPermission();
  }
  if (permission !== "granted") throw new Error("Notification permission was not granted.");
  const serviceWorkerRegistration = await navigator.serviceWorker.register(getServiceWorkerUrl(config));
  const token = await firebase.messaging().getToken({ vapidKey, serviceWorkerRegistration });
  if (!token) throw new Error("This browser could not finish notification setup.");
  window.localStorage.setItem(WEB_PUSH_TOKEN_STORAGE_KEY, token);
  return token;
}

export async function ensureBrowserWebPushRegistration(options: { interactive?: boolean } = {}) {
  const status = await getWebPushStatus();
  if (!status.supported) throw new Error("Web push is not supported in this browser.");
  if (!status.configured) throw new Error("Browser notification configuration is incomplete.");
  if (status.permission === "denied") throw new Error(getWebPushPermissionMessage("denied"));
  if (status.permission !== "granted" && options.interactive === false) return "";
  return registerBrowserForWebPush(options);
}

export async function showChatActivityNotification({
  title,
  body,
  tag,
  data,
}: {
  title: string;
  body: string;
  tag: string;
  data?: Record<string, string>;
}) {
  if (typeof window === "undefined" || !("Notification" in window) || Notification.permission !== "granted") return false;
  const replyAction = data?.conversation_id && data?.message_id && !data?.call_id
    ? [{ action: "reply", title: "Reply" }]
    : [];
  const payload: NotificationOptions & { actions: Array<{ action: string; title: string }> } = {
    body,
    tag,
    data: data ?? {},
    actions: replyAction,
  };
  if ("serviceWorker" in navigator) {
    const registration = await navigator.serviceWorker.getRegistration();
    if (registration) {
      await registration.showNotification(title, payload);
      return true;
    }
  }
  const notification = new Notification(title, payload);
  notification.onclick = () => {
    const href = data?.call_id ? `/calls/${data.call_id}` : data?.conversation_id ? `/chat/${data.conversation_id}?reply=1` : "/";
    window.focus();
    window.location.assign(href);
  };
  return true;
}
