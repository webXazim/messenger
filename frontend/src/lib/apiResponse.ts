
export type CursorPage<T> = {
  results: T[];
  next: string | null;
  previous: string | null;
};

export type ApiEnvelope<T> = {
  success?: boolean;
  message?: string;
  data?: T;
  results?: T;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

export function unwrapData<T>(value: unknown): T {
  if (isRecord(value)) {
    if ('data' in value && (value as ApiEnvelope<T>).data !== undefined) {
      return (value as ApiEnvelope<T>).data as T;
    }
    if ('results' in value && (value as ApiEnvelope<T>).results !== undefined) {
      return (value as ApiEnvelope<T>).results as T;
    }
  }
  return value as T;
}

export function unwrapArray<T>(value: unknown): T[] {
  const unwrapped = unwrapData<unknown>(value);
  return Array.isArray(unwrapped) ? (unwrapped as T[]) : [];
}

export function unwrapObject<T extends Record<string, unknown>>(value: unknown, fallback: T): T {
  const unwrapped = unwrapData<unknown>(value);
  return isRecord(unwrapped) ? (unwrapped as T) : fallback;
}


export function unwrapCursorPage<T>(value: unknown): CursorPage<T> {
  let payload: unknown = value;
  if (isRecord(value) && "data" in value && value.data !== undefined) {
    payload = value.data;
  }

  if (Array.isArray(payload)) {
    return { results: payload as T[], next: null, previous: null };
  }

  const record = isRecord(payload) ? payload : {};
  const results = Array.isArray(record.results)
    ? (record.results as T[])
    : Array.isArray(record.data)
      ? (record.data as T[])
      : [];

  return {
    results,
    next: typeof record.next === "string" && record.next.trim() ? record.next : null,
    previous: typeof record.previous === "string" && record.previous.trim() ? record.previous : null,
  };
}
