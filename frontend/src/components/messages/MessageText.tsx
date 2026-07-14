import type { ReactNode } from "react";

function highlightedText(text: string, query?: string): ReactNode {
  if (!query?.trim()) return text;
  const safe = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(`(${safe})`, "ig");
  return text.split(pattern).map((part, index) => (
    part.toLowerCase() === query.toLowerCase()
      ? <mark key={`${part}-${index}`}>{part}</mark>
      : <span key={`${part}-${index}`}>{part}</span>
  ));
}

export function MessageText({
  text,
  deleted,
  encrypted,
  decryptionState,
  decryptionMessage,
  searchQuery,
}: {
  text: string;
  deleted?: boolean;
  encrypted?: boolean;
  decryptionState?: "pending" | "ready" | "unavailable" | "error";
  decryptionMessage?: string;
  searchQuery?: string;
}) {
  const encryptedFallback = decryptionState === "unavailable"
    ? (decryptionMessage || "This message was not encrypted for this device.")
    : decryptionState === "error"
      ? (decryptionMessage || "This encrypted message could not be opened.")
      : "Decrypting message…";
  const content = deleted
    ? "Message deleted"
    : text
      ? highlightedText(text, searchQuery)
      : encrypted
        ? encryptedFallback
        : "";
  if (!content) return null;
  return (
    <span className={`ms-message-text ${deleted ? "is-deleted" : ""} ${encrypted && !text ? "is-encryption-status" : ""}`}>
      {content}
    </span>
  );
}
