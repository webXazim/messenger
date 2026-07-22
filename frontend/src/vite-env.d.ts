/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_CHAT_CONVERSATION_COMMAND_BACKEND?: string;
  readonly VITE_SUPPORT_DATA_BACKEND?: string;
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_CHAT_COMMAND_BACKEND?: string;
  readonly VITE_CHAT_INTERACTION_BACKEND?: string;
  readonly VITE_CHAT_MESSAGE_MUTATION_BACKEND?: string;
  readonly VITE_CHAT_CALL_RUNTIME_BACKEND?: string;
  readonly VITE_CHAT_READ_BACKEND?: string;
  readonly VITE_CHAT_ATTACHMENT_BACKEND?: string;
  readonly VITE_WS_BASE_URL?: string;
  readonly VITE_SUPPORT_WS_URL?: string;
  readonly VITE_AUTH_BASE_URL?: string;
  readonly VITE_CENTRAL_AUTH_ORIGIN?: string;
  readonly VITE_SOCIAL_BASE_URL?: string;
  readonly VITE_APP_NAME?: string;
  readonly VITE_SUPPORT_PLANS_URL?: string;
  readonly VITE_FIREBASE_API_KEY?: string;
  readonly VITE_FIREBASE_AUTH_DOMAIN?: string;
  readonly VITE_FIREBASE_PROJECT_ID?: string;
  readonly VITE_FIREBASE_STORAGE_BUCKET?: string;
  readonly VITE_FIREBASE_MESSAGING_SENDER_ID?: string;
  readonly VITE_FIREBASE_APP_ID?: string;
  readonly VITE_FIREBASE_VAPID_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
