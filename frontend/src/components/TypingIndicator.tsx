export function TypingIndicator({ names }: { names: string[] }) {
  const visibleNames = names.filter(Boolean);
  const label = visibleNames.length ? `${visibleNames.join(", ")} typing` : "";
  return (
    <div
      className={`ms-typing-indicator${visibleNames.length ? " is-visible" : ""}`}
      role="status"
      aria-live="polite"
      aria-atomic="true"
      aria-hidden={!visibleNames.length}
    >
      <span className="ms-typing-indicator__copy">{label}</span>
      <span className="ms-typing-indicator__dots" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
    </div>
  );
}
