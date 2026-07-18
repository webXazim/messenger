export function safeAppReturnPath(value: string | null | undefined, fallback = "/chat") {
  const candidate = (value || "").trim();
  if (!candidate.startsWith("/") || candidate.startsWith("//") || candidate.includes("\\")) return fallback;
  return candidate;
}
