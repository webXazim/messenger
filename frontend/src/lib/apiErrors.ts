import axios from "axios";

export type ApiFieldErrors = Record<string, string>;

export type ParsedApiError = {
  message: string;
  fields: ApiFieldErrors;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function collectMessages(value: unknown): string[] {
  if (typeof value === "string") {
    const normalized = value.trim();
    if (!normalized || /<(?:!doctype|html|head|body|title|h1)\b/i.test(normalized)) {
      return [];
    }
    return [normalized];
  }
  if (Array.isArray(value)) return value.flatMap(collectMessages);
  if (isRecord(value)) return Object.values(value).flatMap(collectMessages);
  return [];
}

function collectFieldErrors(value: unknown, prefix = "", target: ApiFieldErrors = {}): ApiFieldErrors {
  if (!isRecord(value)) return target;

  for (const [key, nested] of Object.entries(value)) {
    const field = prefix ? `${prefix}.${key}` : key;
    if (isRecord(nested)) {
      collectFieldErrors(nested, field, target);
      continue;
    }
    const messages = collectMessages(nested);
    if (messages.length) target[field] = messages.join(" ");
  }

  return target;
}

export function parseApiError(error: unknown, fallback: string): ParsedApiError {
  if (!axios.isAxiosError(error)) {
    return {
      message: error instanceof Error && error.message ? error.message : fallback,
      fields: {},
    };
  }

  const payload = error.response?.data;
  const record = isRecord(payload) ? payload : {};
  const nestedErrors = isRecord(record.errors) ? record.errors : null;
  const fields = collectFieldErrors(nestedErrors ?? record);

  delete fields.detail;
  delete fields.message;
  delete fields.non_field_errors;
  delete fields.errors;

  const message = collectMessages(record.detail)[0]
    || collectMessages(record.message)[0]
    || collectMessages(record.non_field_errors)[0]
    || collectMessages(nestedErrors)[0]
    || collectMessages(payload)[0]
    || fallback;

  return { message, fields };
}
