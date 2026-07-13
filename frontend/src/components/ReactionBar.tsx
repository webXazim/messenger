import type { Message } from "../types/chat";

export function ReactionBar({
  message,
  onToggle,
  currentUserId,
}: {
  message: Message;
  onToggle: (emoji: string) => void;
  currentUserId?: string;
}) {
  const summaryEntries = Object.entries(message.reaction_summary ?? {}).filter(([, count]) => Number(count) > 0);
  const ownReactions = new Set(
    (message.reactions ?? [])
      .filter((reaction) => String(reaction.user.id) === String(currentUserId || ""))
      .map((reaction) => reaction.emoji),
  );

  if (!summaryEntries.length) return null;

  return (
    <div className="ms-message-reactions">
      <div className="ms-message-reactions__summary">
        {summaryEntries.map(([emoji, count]) => (
          <button
            key={emoji}
            type="button"
            className={ownReactions.has(emoji) ? "is-active" : ""}
            onClick={() => onToggle(emoji)}
            aria-pressed={ownReactions.has(emoji)}
            aria-label={`${emoji} reaction, ${count}`}
          >
            <span>{emoji}</span>
            {Number(count) > 1 ? <small>{count}</small> : null}
          </button>
        ))}
      </div>
    </div>
  );
}
