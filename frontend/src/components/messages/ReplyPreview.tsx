import type { Message } from "../../types/chat";
import { getFallbackMessagePreview } from "./messagePresentation";

export function ReplyPreview({ message, onJumpToReply }: { message: Message; onJumpToReply?: (replyToId: string) => void }) {
  const preview = message.reply_preview;
  if (!preview) return null;

  const content = (
    <>
      <span className="ms-message-reply__label">Reply</span>
      <span className="ms-message-reply__text">{preview.text || getFallbackMessagePreview(message)}</span>
    </>
  );

  if (preview.id && onJumpToReply) {
    return (
      <button type="button" className="ms-message-reply" onClick={() => onJumpToReply(preview.id as string)} aria-label="Jump to replied message">
        {content}
      </button>
    );
  }
  return <div className="ms-message-reply">{content}</div>;
}
