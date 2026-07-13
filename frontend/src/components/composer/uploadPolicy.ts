import type { ChatCapabilities } from "../../api/chat";

export type ComposerUploadPolicy = {
  maxBytes: number;
  allowedExtensions: string[];
  allowedMimeTypes: string[];
  maxParallelUploads: number;
};

export type UploadValidationResult = {
  valid: boolean;
  message?: string;
};

const DEFAULT_MAX_UPLOAD_BYTES = 15 * 1024 * 1024;
const DEFAULT_PARALLEL_UPLOADS = 3;

function stringList(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value.map((entry) => String(entry || "").trim().toLowerCase()).filter(Boolean);
}

function extensionList(value: unknown) {
  return stringList(value)
    .map((extension) => extension.replace(/^\.+/, ""))
    .filter(Boolean);
}

function numericValue(value: unknown, fallback: number) {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export function uploadPolicyFromCapabilities(capabilities?: ChatCapabilities | null): ComposerUploadPolicy {
  return {
    maxBytes: numericValue(capabilities?.limits?.max_upload_bytes, DEFAULT_MAX_UPLOAD_BYTES),
    allowedExtensions: extensionList(capabilities?.media?.allowed_extensions),
    allowedMimeTypes: stringList(capabilities?.media?.allowed_mime_types),
    maxParallelUploads: DEFAULT_PARALLEL_UPLOADS,
  };
}

export function formatUploadLimit(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "the allowed size";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  const megabytes = bytes / (1024 * 1024);
  return `${Number.isInteger(megabytes) ? megabytes.toFixed(0) : megabytes.toFixed(1)} MB`;
}

function extensionForFile(file: File) {
  const match = file.name.toLowerCase().match(/\.([a-z0-9][a-z0-9_-]*)$/i);
  return match?.[1] || "";
}

function mimeAllowed(mime: string, allowedMimeTypes: string[]) {
  if (!allowedMimeTypes.length || !mime) return true;
  return allowedMimeTypes.some((allowed) => {
    if (allowed.endsWith("/*")) return mime.startsWith(allowed.slice(0, -1));
    return mime === allowed;
  });
}

export function validateComposerUpload(file: File, policy: ComposerUploadPolicy): UploadValidationResult {
  if (!file.size) return { valid: false, message: `${file.name || "This file"} is empty.` };
  if (file.size > policy.maxBytes) {
    return { valid: false, message: `${file.name} is larger than the ${formatUploadLimit(policy.maxBytes)} upload limit.` };
  }

  const extension = extensionForFile(file);
  if (policy.allowedExtensions.length && extension && !policy.allowedExtensions.includes(extension)) {
    return { valid: false, message: `${file.name} uses a file type that is not supported.` };
  }

  const mime = (file.type || "").trim().toLowerCase();
  if (!mimeAllowed(mime, policy.allowedMimeTypes)) {
    return { valid: false, message: `${file.name} uses a file type that is not supported.` };
  }

  return { valid: true };
}
