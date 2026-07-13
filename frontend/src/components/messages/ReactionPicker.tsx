const REACTIONS = ["👍", "❤️", "😂", "😮", "😢", "🔥", "🙏", "🎉", "👏", "😁", "😎", "🤔"];

export function ReactionPicker({ onSelect, onClose }: { onSelect: (emoji: string) => void; onClose: () => void }) {
  return (
    <div className="ms-reaction-picker" role="dialog" aria-label="Choose a reaction">
      <div className="ms-reaction-picker__grid">
        {REACTIONS.map((emoji) => (
          <button type="button" key={emoji} onClick={() => onSelect(emoji)} aria-label={`React with ${emoji}`}>{emoji}</button>
        ))}
      </div>
      <button type="button" className="ms-reaction-picker__close" onClick={onClose}>Close</button>
    </div>
  );
}
