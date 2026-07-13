import { useId, useRef, useState } from "react";
import { useModalAccessibility } from "../hooks/useModalAccessibility";
import type { Conversation, Message } from "../types/chat";

export function ForwardMessageModal({
  conversations,
  message,
  onClose,
  onForward,
}: {
  conversations: Conversation[];
  message: Message;
  onClose: () => void;
  onForward: (conversationId: string) => Promise<void>;
}) {
  const [pendingConversationId, setPendingConversationId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const titleId = useId();
  const descriptionId = useId();
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const dialogRef = useModalAccessibility<HTMLElement>({
    onClose,
    initialFocusRef: closeRef,
    closeOnEscape: !pendingConversationId,
  });
  const hasEncryptedAttachments = (message.attachments ?? []).some((attachment) => Boolean(attachment.is_encrypted));
  const canForward = !message.is_encrypted && !hasEncryptedAttachments;
  const preview = message.is_encrypted ? "Encrypted message" : (message.text || "Attachment or voice note");

  const forward = async (conversationId: string) => {
    if (!canForward || pendingConversationId) return;
    try {
      setError(null);
      setPendingConversationId(conversationId);
      await onForward(conversationId);
    } catch (forwardError) {
      setError(forwardError instanceof Error ? forwardError.message : "Could not forward this message.");
    } finally {
      setPendingConversationId(null);
    }
  };

  return (
    <div className="ms-modal-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !pendingConversationId) onClose();
    }}>
      <section
        ref={dialogRef}
        className="ms-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        aria-busy={Boolean(pendingConversationId)}
        tabIndex={-1}
      >
        <header className="ms-modal__header">
          <h3 id={titleId}>Forward message</h3>
          <button ref={closeRef} type="button" className="ms-button ms-button--ghost ms-button--compact" disabled={Boolean(pendingConversationId)} onClick={onClose}>Close</button>
        </header>
        <div id={descriptionId} className="ms-muted">{preview}</div>
        {!canForward ? <div className="ms-modal__notice">Encrypted messages must be decrypted and re-encrypted on the sender device before forwarding.</div> : null}
        {error ? <div className="ms-modal__notice ms-modal__notice--danger" role="alert">{error}</div> : null}
        <div className="ms-modal__list" aria-label="Choose a conversation">
          {conversations.map((conversation) => (
            <button
              key={conversation.id}
              type="button"
              className="ms-modal__list-item"
              disabled={!canForward || Boolean(pendingConversationId)}
              onClick={() => void forward(conversation.id)}
            >
              <strong>{conversation.title || "Untitled conversation"}</strong>
              <span>{pendingConversationId === conversation.id ? "Forwarding…" : conversation.type}</span>
            </button>
          ))}
          {!conversations.length ? <div className="ms-modal__empty">No conversations are available.</div> : null}
        </div>
      </section>
    </div>
  );
}
