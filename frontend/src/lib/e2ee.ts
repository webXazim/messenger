import { chatApi } from "../api/chat";
import { getOrCreateDeviceId } from "./deviceIdentity";
import { safeId } from "./safeId";
import type { AttachmentEncryptionEnvelope, ConversationE2EEKeyMaterial, E2EEDeviceKey, Message, MessageAttachment, MessageEncryptionEnvelope } from "../types/chat";

const STORAGE_PREFIX = "messenger:e2ee:identity:";
const TRUST_STORAGE_PREFIX = "messenger:e2ee:trust:";
const SEEN_STORAGE_PREFIX = "messenger:e2ee:seen:";
const TOFU_STORAGE_PREFIX = "messenger:e2ee:tofu:v1:";
const RSA_ALGORITHM = "RSA-OAEP-256";
const ENVELOPE_VERSION = "v2";
const MESSAGE_ALGORITHM = "aes-256-gcm+rsa-oaep-256";
const ATTACHMENT_ALGORITHM = "aes-256-gcm+rsa-oaep-256:file";

export type E2EEIdentity = {
  userId: string;
  deviceId: string;
  keyId: string;
  algorithm: string;
  publicKeyJwk: JsonWebKey;
  privateKeyJwk: JsonWebKey;
  registrationChanged?: boolean;
};

type StoredIdentity = Omit<E2EEIdentity, "registrationChanged">;
type TrustStore = Record<string, Record<string, true>>;
type SeenStore = Record<string, Record<string, true>>;

export type ConversationSecuritySummary = {
  material: ConversationE2EEKeyMaterial;
  newDeviceCount: number;
  untrustedDeviceCount: number;
  participantCount: number;
};

export type E2EEEnvironmentStatus = {
  available: boolean;
  secureContext: boolean;
  webCrypto: boolean;
  message: string;
};

export type E2EEIssueCode =
  | "secure_context_required"
  | "identity_unavailable"
  | "device_key_revoked"
  | "material_loading"
  | "material_unavailable"
  | "current_device_missing"
  | "participant_device_missing"
  | "stale_key_version"
  | "device_coverage_incomplete"
  | "decryption_key_unavailable"
  | "decryption_failed"
  | "encryption_failed";

export class E2EEPreparationError extends Error {
  code: E2EEIssueCode;
  missingParticipantIds: string[];

  constructor(code: E2EEIssueCode, message: string, missingParticipantIds: string[] = []) {
    super(message);
    this.name = "E2EEPreparationError";
    this.code = code;
    this.missingParticipantIds = missingParticipantIds;
  }
}

export type ConversationEncryptionReadiness = {
  status: "preparing" | "ready" | "blocked";
  code: E2EEIssueCode | "ready" | "rekeying";
  message: string;
  missingParticipantIds: string[];
  canEncrypt: boolean;
};

export type DecryptionResult = {
  status: "ready" | "unavailable" | "error";
  text: string;
  message?: string;
};

function readApiErrorPayload(error: unknown): Record<string, unknown> {
  if (!error || typeof error !== "object" || !("response" in error)) return {};
  const response = (error as { response?: { data?: unknown } }).response;
  return response?.data && typeof response.data === "object" && !Array.isArray(response.data)
    ? response.data as Record<string, unknown>
    : {};
}

function firstApiErrorString(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value) && value.length) return firstApiErrorString(value[0]);
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return firstApiErrorString(record.message) || firstApiErrorString(record.detail);
  }
  return "";
}

function nestedApiErrors(payload: Record<string, unknown>) {
  return payload.errors && typeof payload.errors === "object" && !Array.isArray(payload.errors)
    ? payload.errors as Record<string, unknown>
    : {};
}

export function getE2EEErrorCode(error: unknown): string {
  if (error instanceof E2EEPreparationError) return error.code;
  const payload = readApiErrorPayload(error);
  const errors = nestedApiErrors(payload);
  return firstApiErrorString(errors.code) || firstApiErrorString(payload.code);
}

export function getE2EEErrorMessage(error: unknown, fallback = "Secure messaging could not be prepared.") {
  const payload = readApiErrorPayload(error);
  const errors = nestedApiErrors(payload);
  for (const source of [errors, payload]) {
    for (const key of ["detail", "encryption", "attachment_encryption", "key_id", "message"]) {
      const value = firstApiErrorString(source[key]);
      if (value && value !== "Request failed.") return value;
    }
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

export function getConversationEncryptionReadiness({
  material,
  participantUserIds,
  currentUserId,
  currentKeyId,
  environment = getE2EEEnvironmentStatus(),
  isLoading = false,
  isError = false,
}: {
  material?: ConversationE2EEKeyMaterial;
  participantUserIds: string[];
  currentUserId: string;
  currentKeyId?: string | null;
  environment?: E2EEEnvironmentStatus;
  isLoading?: boolean;
  isError?: boolean;
}): ConversationEncryptionReadiness {
  if (!environment.available) {
    return {
      status: "blocked",
      code: "secure_context_required",
      message: environment.message,
      missingParticipantIds: [],
      canEncrypt: false,
    };
  }
  if (isError) {
    return {
      status: "blocked",
      code: "material_unavailable",
      message: "Secure device information could not be loaded. Retry before sending.",
      missingParticipantIds: [],
      canEncrypt: false,
    };
  }
  if (isLoading || !material) {
    return {
      status: "preparing",
      code: "material_loading",
      message: "Preparing secure messaging…",
      missingParticipantIds: [],
      canEncrypt: false,
    };
  }
  const participantIds = Array.from(new Set(participantUserIds.filter(Boolean)));
  const missingParticipantIds = participantIds.filter((participantId) => !(material.participants[String(participantId)] ?? []).length);
  if (missingParticipantIds.length) {
    return {
      status: "blocked",
      code: "participant_device_missing",
      message: "A participant has not finished secure-device setup yet.",
      missingParticipantIds,
      canEncrypt: false,
    };
  }
  const currentKeys = material.participants[String(currentUserId)] ?? [];
  if (!currentKeyId || !currentKeys.some((key) => key.key_id === currentKeyId)) {
    return {
      status: "preparing",
      code: "current_device_missing",
      message: "Registering this browser for secure messaging…",
      missingParticipantIds: [],
      canEncrypt: false,
    };
  }
  return {
    status: "ready",
    code: material.rekey_required ? "rekeying" : "ready",
    message: material.rekey_required ? "Updating secure-device coverage…" : "Messages are protected automatically.",
    missingParticipantIds: [],
    canEncrypt: true,
  };
}

export function getE2EEEnvironmentStatus(): E2EEEnvironmentStatus {
  if (typeof window === "undefined") {
    return { available: false, secureContext: false, webCrypto: false, message: "Secure messaging is unavailable in this environment." };
  }
  const secureContext = window.isSecureContext;
  const webCrypto = Boolean(window.crypto?.subtle);
  if (!secureContext || !webCrypto) {
    return {
      available: false,
      secureContext,
      webCrypto,
      message: "End-to-end encryption requires a trusted HTTPS connection on this browser.",
    };
  }
  return { available: true, secureContext: true, webCrypto: true, message: "End-to-end encryption is available." };
}

export function getStoredE2EEIdentity(userId: string): { deviceId: string; keyId: string } | null {
  if (typeof window === "undefined" || !userId) return null;
  try {
    const raw = window.localStorage.getItem(getStorageKey(userId));
    if (!raw) return null;
    const identity = JSON.parse(raw) as StoredIdentity;
    if (!identity?.deviceId || !identity?.keyId) return null;
    return { deviceId: identity.deviceId, keyId: identity.keyId };
  } catch {
    return null;
  }
}

function getStorageKey(userId: string) {
  return `${STORAGE_PREFIX}${userId}`;
}

function getTrustStorageKey(userId: string) {
  return `${TRUST_STORAGE_PREFIX}${userId}`;
}

function getSeenStorageKey(userId: string) {
  return `${SEEN_STORAGE_PREFIX}${userId}`;
}

function hasWebCrypto() {
  return getE2EEEnvironmentStatus().available;
}

function bytesToBase64(bytes: Uint8Array) {
  let binary = "";
  for (const value of bytes) binary += String.fromCharCode(value);
  return window.btoa(binary);
}

function base64ToBytes(value: string) {
  const binary = window.atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function stableJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map((item) => stableJson(item)).join(",")}]`;
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record).sort();
  return `{${keys.map((key) => `${JSON.stringify(key)}:${stableJson(record[key])}`).join(",")}}`;
}

function encodeUtf8(value: string) {
  return new TextEncoder().encode(value);
}

function toArrayBuffer(bytes: Uint8Array) {
  const copy = new Uint8Array(bytes.byteLength);
  copy.set(bytes);
  return copy.buffer;
}

function buildAadCandidates(aad: Record<string, unknown> | undefined, kind: "message" | "attachment") {
  const source = aad && typeof aad === "object" ? aad : {};
  const candidates: Uint8Array[] = [];
  const seen = new Set<string>();
  const push = (json: string) => {
    if (!json || seen.has(json)) return;
    seen.add(json);
    candidates.push(encodeUtf8(json));
  };

  push(stableJson(source));
  push(JSON.stringify(source));

  if (kind === "message") {
    push(JSON.stringify({
      conversation_id: source.conversation_id,
      version: source.version,
    }));
  } else {
    push(JSON.stringify({
      conversation_id: source.conversation_id,
      version: source.version,
      kind: source.kind,
    }));
    push(JSON.stringify({
      conversation_id: source.conversation_id,
      kind: source.kind,
      version: source.version,
    }));
  }

  push("{}");
  return candidates;
}

async function decryptWithAadCandidates({
  key,
  nonce,
  ciphertext,
  aad,
  kind,
}: {
  key: CryptoKey;
  nonce: Uint8Array;
  ciphertext: Uint8Array;
  aad?: Record<string, unknown>;
  kind: "message" | "attachment";
}) {
  const candidates = buildAadCandidates(aad, kind);
  let lastError: unknown = null;
  for (const additionalData of candidates) {
    try {
      return await window.crypto.subtle.decrypt(
        {
          name: "AES-GCM",
          iv: toArrayBuffer(nonce),
          additionalData: toArrayBuffer(additionalData),
        },
        key,
        toArrayBuffer(ciphertext),
      );
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError ?? new Error("Unable to decrypt payload.");
}

async function sha256Base64Url(text: string) {
  const data = new TextEncoder().encode(text);
  const digest = await window.crypto.subtle.digest("SHA-256", data);
  return bytesToBase64(new Uint8Array(digest)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "").slice(0, 32);
}

function readJsonStorage<T>(storageKey: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function writeJsonStorage(storageKey: string, value: unknown) {
  window.localStorage.setItem(storageKey, JSON.stringify(value));
}

async function generateIdentity(userId: string): Promise<StoredIdentity> {
  const keyPair = await window.crypto.subtle.generateKey(
    {
      name: "RSA-OAEP",
      modulusLength: 2048,
      publicExponent: new Uint8Array([1, 0, 1]),
      hash: "SHA-256",
    },
    true,
    ["encrypt", "decrypt"],
  );
  const publicKeyJwk = (await window.crypto.subtle.exportKey("jwk", keyPair.publicKey)) as JsonWebKey;
  const privateKeyJwk = (await window.crypto.subtle.exportKey("jwk", keyPair.privateKey)) as JsonWebKey;
  const deviceId = getOrCreateDeviceId();
  const fingerprint = await sha256Base64Url(JSON.stringify(publicKeyJwk));
  return {
    userId,
    deviceId,
    keyId: `rsa-oaep:${deviceId}:${fingerprint}`,
    algorithm: RSA_ALGORITHM,
    publicKeyJwk,
    privateKeyJwk,
  };
}

async function registerStoredIdentity(identity: StoredIdentity) {
  return chatApi.registerE2EEDeviceKey({
    device_id: identity.deviceId,
    key_id: identity.keyId,
    label: "This browser",
    algorithm: identity.algorithm,
    public_key_jwk: identity.publicKeyJwk,
  });
}

const identitySyncPromises = new Map<string, Promise<E2EEIdentity | null>>();

async function syncE2EEIdentity(userId: string): Promise<E2EEIdentity | null> {
  if (!hasWebCrypto() || !userId) return null;
  const storageKey = getStorageKey(userId);
  const existing = window.localStorage.getItem(storageKey);
  let identity: StoredIdentity | null = null;
  if (existing) {
    try {
      identity = JSON.parse(existing) as StoredIdentity;
    } catch {
      identity = null;
    }
  }
  if (
    !identity?.publicKeyJwk
    || !identity?.privateKeyJwk
    || !identity?.keyId
    || !identity?.deviceId
    || identity.userId !== userId
  ) {
    identity = await generateIdentity(userId);
    window.localStorage.setItem(storageKey, JSON.stringify(identity));
  }

  let registered;
  try {
    registered = await registerStoredIdentity(identity);
  } catch (error) {
    if (getE2EEErrorCode(error) !== "e2ee_device_key_revoked") throw error;
    // A revoked private key is never brought back. The stable browser device id
    // lets the backend retire the old key while this signed-in browser creates a
    // fresh identity for future messages.
    identity = await generateIdentity(userId);
    window.localStorage.setItem(storageKey, JSON.stringify(identity));
    registered = await registerStoredIdentity(identity);
  }

  if (registered?.key_id && (registered.key_id !== identity.keyId || (registered.device_id && registered.device_id !== identity.deviceId))) {
    identity = {
      ...identity,
      keyId: registered.key_id,
      deviceId: registered.device_id || identity.deviceId,
    };
    window.localStorage.setItem(storageKey, JSON.stringify(identity));
  }
  return { ...identity, registrationChanged: Boolean(registered?.security_changed) };
}

export async function ensureE2EEIdentity(userId: string): Promise<E2EEIdentity | null> {
  if (!userId) return null;
  const inFlight = identitySyncPromises.get(userId);
  if (inFlight) return inFlight;
  const promise = syncE2EEIdentity(userId).finally(() => {
    if (identitySyncPromises.get(userId) === promise) identitySyncPromises.delete(userId);
  });
  identitySyncPromises.set(userId, promise);
  return promise;
}

async function importRecipientPublicKey(key: E2EEDeviceKey) {
  return window.crypto.subtle.importKey(
    "jwk",
    key.public_key_jwk,
    {
      name: "RSA-OAEP",
      hash: "SHA-256",
    },
    false,
    ["encrypt"],
  );
}

async function importPrivateKey(identity: StoredIdentity) {
  return window.crypto.subtle.importKey(
    "jwk",
    identity.privateKeyJwk,
    {
      name: "RSA-OAEP",
      hash: "SHA-256",
    },
    false,
    ["decrypt"],
  );
}

function uniqueKeys(material: ConversationE2EEKeyMaterial) {
  const seen = new Set<string>();
  const keys: E2EEDeviceKey[] = [];
  for (const participantKeys of Object.values(material.participants)) {
    for (const key of participantKeys) {
      if (!key.key_id || seen.has(key.key_id)) continue;
      seen.add(key.key_id);
      keys.push(key);
    }
  }
  return keys;
}

async function buildWrappedKeys(rawKey: Uint8Array, recipientKeys: E2EEDeviceKey[]) {
  const keyBuffer = new ArrayBuffer(rawKey.byteLength);
  new Uint8Array(keyBuffer).set(rawKey);
  return Promise.all(
    recipientKeys.map(async (key) => {
      const publicKey = await importRecipientPublicKey(key);
      const wrapped = await window.crypto.subtle.encrypt({ name: "RSA-OAEP" }, publicKey, keyBuffer);
      return {
        key_id: key.key_id,
        wrapped_key: bytesToBase64(new Uint8Array(wrapped)),
      };
    }),
  );
}

async function resolveConversationRecipients(
  userId: string,
  conversationId: string,
  expectedParticipantIds: string[] = [],
) {
  if (!hasWebCrypto()) {
    throw new E2EEPreparationError(
      "secure_context_required",
      "End-to-end encryption requires a trusted HTTPS connection on this browser.",
    );
  }
  const identity = await ensureE2EEIdentity(userId);
  if (!identity) {
    throw new E2EEPreparationError("identity_unavailable", "This browser could not create its secure device identity.");
  }
  let material: ConversationE2EEKeyMaterial;
  try {
    material = await chatApi.getConversationE2EEKeys(conversationId);
  } catch (error) {
    throw new E2EEPreparationError("material_unavailable", getE2EEErrorMessage(error, "Secure device information could not be loaded."));
  }
  const participantIds = Array.from(new Set(expectedParticipantIds.filter(Boolean)));
  const missingParticipantIds = participantIds.filter((participantId) => !(material.participants[String(participantId)] ?? []).length);
  if (missingParticipantIds.length) {
    throw new E2EEPreparationError(
      "participant_device_missing",
      "A participant has not finished secure-device setup yet.",
      missingParticipantIds,
    );
  }
  const recipientKeys = uniqueKeys(material);
  if (!recipientKeys.length) {
    throw new E2EEPreparationError("participant_device_missing", "No active secure devices are available for this conversation.");
  }
  if (!recipientKeys.some((key) => key.key_id === identity.keyId)) {
    throw new E2EEPreparationError("current_device_missing", "This browser is still being registered for secure messaging.");
  }
  return { identity, recipientKeys, material };
}

export async function encryptMessageForConversation({
  userId,
  conversationId,
  plaintext,
  participantUserIds = [],
}: {
  userId: string;
  conversationId: string;
  plaintext: string;
  participantUserIds?: string[];
}): Promise<MessageEncryptionEnvelope> {
  if (!plaintext.trim()) throw new E2EEPreparationError("encryption_failed", "Message text is required before encryption.");
  const resolved = await resolveConversationRecipients(userId, conversationId, participantUserIds);
  const { identity, recipientKeys, material } = resolved;

  const messageKey = await window.crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, true, ["encrypt", "decrypt"]);
  const rawMessageKey = new Uint8Array(await window.crypto.subtle.exportKey("raw", messageKey));
  const nonce = window.crypto.getRandomValues(new Uint8Array(12));
  const aad = {
    conversation_id: conversationId,
    version: ENVELOPE_VERSION,
  };
  const ciphertext = await window.crypto.subtle.encrypt(
    {
      name: "AES-GCM",
      iv: nonce,
      additionalData: encodeUtf8(stableJson(aad)),
    },
    messageKey,
    new TextEncoder().encode(plaintext),
  );

  const encryptedKeys = await buildWrappedKeys(rawMessageKey, recipientKeys);

  return {
    version: ENVELOPE_VERSION,
    algorithm: MESSAGE_ALGORITHM,
    ciphertext: bytesToBase64(new Uint8Array(ciphertext)),
    nonce: bytesToBase64(nonce),
    sender_key_id: identity.keyId,
    sender_device_id: identity.deviceId,
    key_version: material.key_version,
    recipient_key_ids: encryptedKeys.map((entry) => entry.key_id),
    encrypted_keys: encryptedKeys,
    aad,
  };
}

async function decryptWrappedKey(identity: StoredIdentity, encryptedKeys: { key_id: string; wrapped_key: string }[]) {
  const wrapped = (encryptedKeys ?? []).find((entry) => entry.key_id === identity.keyId);
  if (!wrapped) return null;
  const privateKey = await importPrivateKey(identity);
  return window.crypto.subtle.decrypt({ name: "RSA-OAEP" }, privateKey, base64ToBytes(wrapped.wrapped_key));
}

export async function decryptMessageTextResult(userId: string, message: Message, providedIdentity?: E2EEIdentity | null): Promise<DecryptionResult> {
  if (!message.is_encrypted || !message.encryption) {
    return { status: "ready", text: message.text || "" };
  }
  if (!hasWebCrypto()) {
    return {
      status: "unavailable",
      text: "",
      message: "This encrypted message requires a trusted HTTPS connection.",
    };
  }
  const identity = providedIdentity ?? await ensureE2EEIdentity(userId);
  if (!identity) {
    return { status: "unavailable", text: "", message: "This browser has no secure device identity." };
  }
  try {
    const rawMessageKey = await decryptWrappedKey(identity, message.encryption.encrypted_keys ?? []);
    if (!rawMessageKey) {
      return {
        status: "unavailable",
        text: "",
        message: "This message was encrypted before this device was linked.",
      };
    }
    const messageKey = await window.crypto.subtle.importKey("raw", rawMessageKey, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
    const plaintext = await decryptWithAadCandidates({
      key: messageKey,
      nonce: base64ToBytes(message.encryption.nonce),
      ciphertext: base64ToBytes(message.encryption.ciphertext),
      aad: message.encryption.aad ?? {},
      kind: "message",
    });
    return { status: "ready", text: new TextDecoder().decode(plaintext) };
  } catch {
    return {
      status: "error",
      text: "",
      message: "This encrypted message could not be opened on this device.",
    };
  }
}

export async function decryptMessageText(userId: string, message: Message) {
  const result = await decryptMessageTextResult(userId, message);
  return result.text;
}

export async function encryptAttachmentForConversation({
  userId,
  conversationId,
  file,
  participantUserIds = [],
  previewBlob,
}: {
  userId: string;
  conversationId: string;
  file: File;
  participantUserIds?: string[];
  previewBlob?: Blob | null;
}) {
  const resolved = await resolveConversationRecipients(userId, conversationId, participantUserIds);
  const { identity, recipientKeys, material } = resolved;
  const rawBytes = new Uint8Array(await file.arrayBuffer());
  const fileKey = await window.crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, true, ["encrypt", "decrypt"]);
  const rawFileKey = new Uint8Array(await window.crypto.subtle.exportKey("raw", fileKey));
  const fileNonce = window.crypto.getRandomValues(new Uint8Array(12));
  const metadataNonce = window.crypto.getRandomValues(new Uint8Array(12));
  const previewNonce = previewBlob?.size ? window.crypto.getRandomValues(new Uint8Array(12)) : null;
  const aad = { conversation_id: conversationId, version: ENVELOPE_VERSION, kind: "attachment" };
  const encryptedFile = await window.crypto.subtle.encrypt(
    { name: "AES-GCM", iv: fileNonce, additionalData: encodeUtf8(stableJson(aad)) },
    fileKey,
    rawBytes,
  );
  const metadataCiphertext = await window.crypto.subtle.encrypt(
    { name: "AES-GCM", iv: metadataNonce, additionalData: encodeUtf8(stableJson(aad)) },
    fileKey,
    new TextEncoder().encode(JSON.stringify({ name: file.name, mime_type: file.type || "application/octet-stream", size: file.size })),
  );
  const previewCiphertext = previewBlob?.size && previewNonce
    ? await window.crypto.subtle.encrypt(
        { name: "AES-GCM", iv: previewNonce, additionalData: encodeUtf8(stableJson(aad)) },
        fileKey,
        await previewBlob.arrayBuffer(),
      )
    : null;
  const digest = await window.crypto.subtle.digest("SHA-256", rawBytes);
  const encryptedKeys = await buildWrappedKeys(rawFileKey, recipientKeys);
  const extension = file.name.includes(".") ? file.name.split(".").pop() : "bin";
  const encryptedMimeType = file.type || "application/octet-stream";
  const blob = new Blob([encryptedFile], { type: encryptedMimeType });
  const encryptedFileName = `encrypted-${safeId("blob")}.${extension || "bin"}`;
  return {
    uploadFile: new File([blob], encryptedFileName, { type: encryptedMimeType }),
    envelope: {
      version: ENVELOPE_VERSION,
      algorithm: ATTACHMENT_ALGORITHM,
      nonce: bytesToBase64(fileNonce),
      sender_key_id: identity.keyId,
      sender_device_id: identity.deviceId,
      key_version: material.key_version,
      recipient_key_ids: encryptedKeys.map((entry) => entry.key_id),
      encrypted_keys: encryptedKeys,
      metadata_ciphertext: bytesToBase64(new Uint8Array(metadataCiphertext)),
      metadata_nonce: bytesToBase64(metadataNonce),
      original_sha256: bytesToBase64(new Uint8Array(digest)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, ""),
      preview_ciphertext: previewCiphertext ? bytesToBase64(new Uint8Array(previewCiphertext)) : undefined,
      preview_nonce: previewNonce ? bytesToBase64(previewNonce) : undefined,
      preview_mime_type: previewBlob?.type || undefined,
      aad,
    } satisfies AttachmentEncryptionEnvelope,
  };
}

export async function decryptAttachmentPreview(userId: string, attachment: MessageAttachment) {
  const envelope = attachment.encryption;
  if (!attachment.is_encrypted || !envelope?.preview_ciphertext || !envelope.preview_nonce) return null;
  if (!hasWebCrypto()) return null;
  const identity = await ensureE2EEIdentity(userId);
  if (!identity) return null;
  const rawKey = await decryptWrappedKey(identity, envelope.encrypted_keys ?? []);
  if (!rawKey) return null;
  const fileKey = await window.crypto.subtle.importKey("raw", rawKey, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
  const bytes = await decryptWithAadCandidates({
    key: fileKey,
    nonce: base64ToBytes(envelope.preview_nonce),
    ciphertext: base64ToBytes(envelope.preview_ciphertext),
    aad: envelope.aad ?? {},
    kind: "attachment",
  });
  return new Blob([bytes], { type: envelope.preview_mime_type || "image/jpeg" });
}

export async function rewrapAttachmentEncryptionForConversation({
  userId,
  conversationId,
  envelope,
  participantUserIds = [],
}: {
  userId: string;
  conversationId: string;
  envelope: AttachmentEncryptionEnvelope;
  participantUserIds?: string[];
}): Promise<AttachmentEncryptionEnvelope> {
  const { identity, recipientKeys, material } = await resolveConversationRecipients(
    userId,
    conversationId,
    participantUserIds,
  );
  const rawKey = await decryptWrappedKey(identity, envelope.encrypted_keys ?? []);
  if (!rawKey) {
    throw new E2EEPreparationError(
      "decryption_key_unavailable",
      "This device no longer has the attachment key required to retry this upload.",
    );
  }
  const encryptedKeys = await buildWrappedKeys(new Uint8Array(rawKey), recipientKeys);
  return {
    ...envelope,
    key_version: material.key_version,
    recipient_key_ids: encryptedKeys.map((entry) => entry.key_id),
    encrypted_keys: encryptedKeys,
  };
}

export async function decryptAttachment(userId: string, attachment: MessageAttachment, encryptedBlob: Blob) {
  if (!attachment.is_encrypted || !attachment.encryption) {
    return { blob: encryptedBlob, name: attachment.original_name, mimeType: attachment.mime_type };
  }
  if (!hasWebCrypto()) {
    throw new E2EEPreparationError("secure_context_required", "This encrypted attachment requires a trusted HTTPS connection.");
  }
  const identity = await ensureE2EEIdentity(userId);
  if (!identity) {
    throw new E2EEPreparationError("identity_unavailable", "This browser has no secure device identity.");
  }
  const rawKey = await decryptWrappedKey(identity, attachment.encryption.encrypted_keys ?? []);
  if (!rawKey) {
    throw new E2EEPreparationError(
      "decryption_key_unavailable",
      "This attachment was encrypted before this device was linked.",
    );
  }
  const fileKey = await window.crypto.subtle.importKey("raw", rawKey, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
  const metadataPlaintext = await decryptWithAadCandidates({
    key: fileKey,
    nonce: base64ToBytes(attachment.encryption.metadata_nonce),
    ciphertext: base64ToBytes(attachment.encryption.metadata_ciphertext),
    aad: attachment.encryption.aad ?? {},
    kind: "attachment",
  });
  const manifest = JSON.parse(new TextDecoder().decode(metadataPlaintext)) as { name?: string; mime_type?: string; size?: number };
  const decryptedBytes = await decryptWithAadCandidates({
    key: fileKey,
    nonce: base64ToBytes(attachment.encryption.nonce),
    ciphertext: new Uint8Array(await encryptedBlob.arrayBuffer()),
    aad: attachment.encryption.aad ?? {},
    kind: "attachment",
  });
  const mimeType = manifest.mime_type || attachment.mime_type || "application/octet-stream";
  return {
    blob: new Blob([decryptedBytes], { type: mimeType }),
    name: manifest.name || attachment.original_name,
    mimeType,
  };
}

function getTrustStore(userId: string) {
  return readJsonStorage<TrustStore>(getTrustStorageKey(userId), {});
}

function getSeenStore(userId: string) {
  return readJsonStorage<SeenStore>(getSeenStorageKey(userId), {});
}

function saveTrustStore(userId: string, store: TrustStore) {
  writeJsonStorage(getTrustStorageKey(userId), store);
}

function saveSeenStore(userId: string, store: SeenStore) {
  writeJsonStorage(getSeenStorageKey(userId), store);
}

function keyFingerprint(key: E2EEDeviceKey) {
  return key.fingerprint || key.key_id;
}

export function formatFingerprint(fingerprint?: string | null) {
  const compact = String(fingerprint || "").replace(/[^a-zA-Z0-9]/g, "");
  if (!compact) return "Unavailable";
  return compact.match(/.{1,4}/g)?.join(" ") || compact;
}

export function listTrustedFingerprints(userId: string, participantUserId: string) {
  const trustStore = getTrustStore(userId);
  return Object.keys(trustStore[String(participantUserId)] || {});
}

export function trustDeviceFingerprint(userId: string, participantUserId: string, fingerprint: string) {
  if (!userId || !participantUserId || !fingerprint) return;
  const trustStore = getTrustStore(userId);
  trustStore[String(participantUserId)] = {
    ...(trustStore[String(participantUserId)] || {}),
    [fingerprint]: true,
  };
  saveTrustStore(userId, trustStore);
}

export function untrustDeviceFingerprint(userId: string, participantUserId: string, fingerprint: string) {
  if (!userId || !participantUserId || !fingerprint) return;
  const trustStore = getTrustStore(userId);
  const participantTrust = { ...(trustStore[String(participantUserId)] || {}) };
  delete participantTrust[fingerprint];
  trustStore[String(participantUserId)] = participantTrust;
  saveTrustStore(userId, trustStore);
}

/**
 * Trust-on-first-use keeps normal conversations frictionless. The device set
 * present when a conversation is first opened on this browser is accepted
 * automatically. Later device changes stay visible in the optional security
 * details panel without blocking messages.
 */
export function establishConversationTrustOnFirstUse(userId: string, material: ConversationE2EEKeyMaterial) {
  if (!userId || !material.conversation_id) return;
  const migrationKey = `${TOFU_STORAGE_PREFIX}${userId}:${material.conversation_id}`;
  if (window.localStorage.getItem(migrationKey)) return;

  const trustStore = getTrustStore(userId);
  let changed = false;

  for (const [participantUserId, keys] of Object.entries(material.participants)) {
    if (String(participantUserId) === String(userId)) continue;
    const participantTrust = { ...(trustStore[participantUserId] || {}) };
    for (const key of keys) {
      const fingerprint = keyFingerprint(key);
      if (!fingerprint || participantTrust[fingerprint]) continue;
      participantTrust[fingerprint] = true;
      changed = true;
    }
    trustStore[participantUserId] = participantTrust;
  }

  if (changed) saveTrustStore(userId, trustStore);
  window.localStorage.setItem(migrationKey, new Date().toISOString());
}

export function inspectConversationSecurity(userId: string, material: ConversationE2EEKeyMaterial): ConversationSecuritySummary {
  const trustStore = getTrustStore(userId);
  const seenStore = getSeenStore(userId);
  let newDeviceCount = 0;
  let untrustedDeviceCount = 0;
  let participantCount = 0;

  for (const [participantUserId, keys] of Object.entries(material.participants)) {
    participantCount += 1;
    if (String(participantUserId) === String(userId)) continue;
    const trusted = trustStore[participantUserId] || {};
    const seen = seenStore[participantUserId] || {};
    for (const key of keys) {
      const fingerprint = keyFingerprint(key);
      if (!fingerprint) continue;
      if (!seen[fingerprint]) newDeviceCount += 1;
      if (!trusted[fingerprint]) untrustedDeviceCount += 1;
    }
  }
  return { material, newDeviceCount, untrustedDeviceCount, participantCount };
}

export function markConversationDevicesSeen(userId: string, material: ConversationE2EEKeyMaterial) {
  const seenStore = getSeenStore(userId);
  for (const [participantUserId, keys] of Object.entries(material.participants)) {
    seenStore[participantUserId] = {
      ...(seenStore[participantUserId] || {}),
      ...Object.fromEntries(keys.map((key) => [keyFingerprint(key), true])),
    };
  }
  saveSeenStore(userId, seenStore);
}
