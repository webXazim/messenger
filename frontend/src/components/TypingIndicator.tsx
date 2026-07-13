export function TypingIndicator({ names }: { names: string[] }) {
  if (!names.length) return null;
  return <div className="ms-typing-indicator" role="status" aria-live="polite" aria-atomic="true">{names.join(", ")} typing…</div>;
}
