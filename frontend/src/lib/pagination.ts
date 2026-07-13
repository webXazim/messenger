export type CursorPage<T> = {
  results: T[];
  next: string | null;
  previous: string | null;
};

export type CollectCursorPagesOptions<T> = {
  signal?: AbortSignal;
  getKey?: (item: T) => string;
  maxPages?: number;
  baseUrl?: string;
};

function runtimeOrigin() {
  if (typeof window !== "undefined" && window.location?.origin) return window.location.origin;
  return "http://localhost";
}

function ensureTrailingSlash(value: string) {
  return value.endsWith("/") ? value : `${value}/`;
}

export function resolveApiCursorUrl(value: string, baseUrl = "/api/v1") {
  if (!value) return value;
  const runtimeBase = new URL(ensureTrailingSlash(baseUrl), runtimeOrigin());
  let target: URL;

  if (/^https?:\/\//i.test(value)) {
    target = new URL(value);
  } else if (value.startsWith("/api/")) {
    target = new URL(value, runtimeBase.origin);
  } else if (value.startsWith("/")) {
    target = new URL(value.slice(1), ensureTrailingSlash(runtimeBase.toString()));
  } else {
    target = new URL(value, ensureTrailingSlash(runtimeBase.toString()));
  }

  // Reverse proxies can emit pagination links with the internal API host or
  // without the public browser port. Keep same-host API cursors on the
  // configured public API origin.
  const internalHosts = new Set(["localhost", "127.0.0.1", "nginx", "web", "backend", "api"]);
  if (
    target.pathname.startsWith("/api/")
    && (target.hostname === runtimeBase.hostname || internalHosts.has(target.hostname.toLowerCase()))
  ) {
    target.protocol = runtimeBase.protocol;
    target.host = runtimeBase.host;
  }

  return target.toString();
}

function throwIfAborted(signal?: AbortSignal) {
  if (!signal?.aborted) return;
  throw signal.reason instanceof Error
    ? signal.reason
    : new DOMException("The request was cancelled.", "AbortError");
}

export async function collectCursorPages<T>(
  initialUrl: string,
  fetchPage: (url: string, signal?: AbortSignal) => Promise<CursorPage<T>>,
  options: CollectCursorPagesOptions<T> = {},
): Promise<T[]> {
  const maxPages = Math.max(1, options.maxPages ?? 200);
  const visited = new Set<string>();
  const ordered: T[] = [];
  const positions = new Map<string, number>();
  let nextUrl: string | null = resolveApiCursorUrl(initialUrl, options.baseUrl);
  let pageCount = 0;

  while (nextUrl) {
    throwIfAborted(options.signal);
    if (visited.has(nextUrl)) {
      throw new Error("The server returned a repeated pagination cursor.");
    }
    if (pageCount >= maxPages) {
      throw new Error(`The result exceeded the safe pagination limit of ${maxPages} pages.`);
    }

    visited.add(nextUrl);
    const page = await fetchPage(nextUrl, options.signal);
    pageCount += 1;

    for (const item of page.results) {
      if (!options.getKey) {
        ordered.push(item);
        continue;
      }
      const key = options.getKey(item);
      if (!key) {
        ordered.push(item);
        continue;
      }
      const existingIndex = positions.get(key);
      if (existingIndex === undefined) {
        positions.set(key, ordered.length);
        ordered.push(item);
      } else {
        // A realtime update can move an item between pages while the request is
        // in flight. Keep one row and prefer the newest server representation.
        ordered[existingIndex] = item;
      }
    }

    if (!page.next) {
      nextUrl = null;
    } else if (/^https?:\/\//i.test(page.next) || page.next.startsWith("/")) {
      nextUrl = resolveApiCursorUrl(page.next, options.baseUrl);
    } else {
      nextUrl = resolveApiCursorUrl(new URL(page.next, nextUrl).toString(), options.baseUrl);
    }
  }

  return ordered;
}
