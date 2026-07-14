import { useEffect, useMemo, useRef, useState, type TouchEventHandler } from "react";
import type { Message } from "../types/chat";
import { ReactionBar } from "./ReactionBar";
import { AudioMessagePlayer } from "./AudioMessagePlayer";
import { MessageActions } from "./messages/MessageActions";
import { ReplyPreview } from "./messages/ReplyPreview";
import { CallEventMessage } from "./messages/CallEventMessage";
import { MediaMessage } from "./messages/MediaMessage";
import { AttachmentMessage } from "./messages/AttachmentMessage";
import { MessageText } from "./messages/MessageText";
import { MessageMeta } from "./messages/MessageMeta";
import { UserAvatar } from "./UserAvatar";
import { prefetchAttachmentForUser } from "./AuthenticatedMedia";
import {
  getAttachmentPlaybackUrl,
  getCallEventPresentation,
  splitAttachments,
} from "./messages/messagePresentation";

const DOUBLE_TAP_REACTION = "❤️";
const LONG_PRESS_MS = 420;
const SWIPE_REPLY_THRESHOLD = 84;

type TouchState = {
  startX: number;
  currentX: number;
  isTracking: boolean;
};

export type MessageBubbleProps = {
  message: Message;
  own: boolean;
  grouped?: boolean;
  readByNames?: string[];
  deliveredByNames?: string[];
  deliveryStatus?: string;
  showSenderIdentity?: boolean;
  onReply: (message: Message) => void;
  onForward: (message: Message) => void;
  onToggleReaction: (message: Message, emoji: string) => void;
  onEdit: (message: Message) => void;
  onDelete: (message: Message) => void;
  onRetry?: (message: Message) => void;
  onReport?: (message: Message) => void;
  onPreviewAttachment?: (attachmentId: string) => void;
  searchQuery?: string;
  currentUserId?: string;
  onJumpToReply?: (replyToId: string) => void;
  actionError?: string | null;
  actionPending?: boolean;
  warmMedia?: boolean;
};

export function MessageBubble({
  message,
  own,
  grouped = false,
  readByNames = [],
  deliveredByNames = [],
  deliveryStatus,
  showSenderIdentity = true,
  onReply,
  onForward,
  onToggleReaction,
  onEdit,
  onDelete,
  onRetry,
  onReport,
  onPreviewAttachment,
  searchQuery,
  currentUserId,
  onJumpToReply,
  actionError,
  actionPending = false,
  warmMedia = false,
}: MessageBubbleProps) {
  const [showContextMenu, setShowContextMenu] = useState(false);
  const [swipeOffset, setSwipeOffset] = useState(0);
  const touchState = useRef<TouchState>({ startX: 0, currentX: 0, isTracking: false });
  const pressTimerRef = useRef<number | null>(null);

  const resolvedDeliveryStatus = (deliveryStatus || message.delivery_status || "").toLowerCase();
  const isFailed = resolvedDeliveryStatus === "failed";
  const isLocalUnsent = message.id.startsWith("temp-");
  const receiptStatus = isFailed ? "failed" : resolvedDeliveryStatus;
  const isEncrypted = Boolean(message.is_encrypted);
  const hasEncryptedAttachments = (message.attachments ?? []).some((attachment) => Boolean(attachment.is_encrypted));
  const hasViewOnceAttachments = (message.attachments ?? []).some((attachment) => Boolean(attachment.view_once));
  const canForward = !message.is_deleted && !isFailed && !isLocalUnsent && !isEncrypted && !hasEncryptedAttachments && !hasViewOnceAttachments;
  const { media, audio, files } = useMemo(() => splitAttachments(message), [message]);
  const callEvent = useMemo(() => getCallEventPresentation(message), [message]);
  const readLabel = readByNames.length
    ? `Seen by ${readByNames.slice(0, 3).join(", ")}${readByNames.length > 3 ? ` +${readByNames.length - 3}` : ""}`
    : "";
  const deliveredLabel = !readLabel && deliveredByNames.length
    ? `Delivered to ${deliveredByNames.slice(0, 3).join(", ")}${deliveredByNames.length > 3 ? ` +${deliveredByNames.length - 3}` : ""}`
    : "";
  const receiptSummary = readLabel || deliveredLabel;
  const senderName = message.sender.display_name || message.sender.username || "User";
  const showAvatar = !own && showSenderIdentity;
  const showAuthor = !grouped && showSenderIdentity;
  const hasText = Boolean(message.text || message.is_deleted || isEncrypted) && !callEvent;
  const hasCopySurface = Boolean(message.reply_preview || message.transcript?.text || message.links?.length || hasText);
  const hasRichContent = Boolean(callEvent || media.length || audio.length || files.length || message.voice_note?.is_voice_note);
  const richOnly = !hasCopySurface && hasRichContent;
  const reactedWithHeart = useMemo(
    () => (message.reactions ?? []).some((reaction) => reaction.emoji === DOUBLE_TAP_REACTION),
    [message.reactions],
  );

  const clearLongPress = () => {
    if (pressTimerRef.current) {
      window.clearTimeout(pressTimerRef.current);
      pressTimerRef.current = null;
    }
  };

  useEffect(() => () => clearLongPress(), []);

  useEffect(() => {
    if (!warmMedia || !message.attachments?.length) return;
    const connection = (navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } }).connection;
    if (connection?.saveData || ["slow-2g", "2g"].includes(connection?.effectiveType || "")) return;
    const eligible = message.attachments
      .filter((attachment) => {
        if (attachment.view_once) return false;
        const kind = (attachment.media_kind || attachment.mime_type || "").toLowerCase();
        return !kind.startsWith("video") && !kind.startsWith("audio") && attachment.size > 0 && attachment.size <= 8 * 1024 * 1024;
      })
      .reduce<{ attachment: typeof message.attachments[number]; total: number }[]>((items, attachment) => {
        const total = (items.length ? items[items.length - 1]!.total : 0) + attachment.size;
        if (total <= 12 * 1024 * 1024) items.push({ attachment, total });
        return items;
      }, []);
    if (!eligible.length) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void Promise.allSettled(eligible.map(({ attachment }) => {
        const src = getAttachmentPlaybackUrl(attachment);
        return src ? prefetchAttachmentForUser(src, controller.signal) : Promise.resolve();
      }));
    }, 3200);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [message.attachments, warmMedia]);

  const startLongPress = () => {
    clearLongPress();
    pressTimerRef.current = window.setTimeout(() => {
      setShowContextMenu(true);
      pressTimerRef.current = null;
    }, LONG_PRESS_MS);
  };

  const resetSwipe = () => {
    touchState.current = { startX: 0, currentX: 0, isTracking: false };
    setSwipeOffset(0);
  };

  const handleTouchStart: TouchEventHandler<HTMLElement> = (event) => {
    const point = event.touches[0];
    if (!point) return;
    touchState.current = { startX: point.clientX, currentX: point.clientX, isTracking: true };
    startLongPress();
  };

  const handleTouchMove: TouchEventHandler<HTMLElement> = (event) => {
    const point = event.touches[0];
    if (!point || !touchState.current.isTracking) return;
    touchState.current.currentX = point.clientX;
    const deltaX = point.clientX - touchState.current.startX;
    if (Math.abs(deltaX) > 8) clearLongPress();
    const directionalOffset = own ? Math.min(0, deltaX) : Math.max(0, deltaX);
    setSwipeOffset(Math.max(-108, Math.min(108, directionalOffset)));
  };

  const handleTouchEnd: TouchEventHandler<HTMLElement> = () => {
    clearLongPress();
    const deltaX = touchState.current.currentX - touchState.current.startX;
    const qualifiesForReply = own ? deltaX <= -SWIPE_REPLY_THRESHOLD : deltaX >= SWIPE_REPLY_THRESHOLD;
    if (qualifiesForReply) onReply(message);
    resetSwipe();
  };

  const handleReact = (emoji: string) => {
    onToggleReaction(message, emoji);
    setShowContextMenu(false);
  };

  return (
    <div className={`ms-message-row ${own ? "ms-message-row--own" : "ms-message-row--incoming"} ${grouped ? "is-grouped" : ""} ${showContextMenu ? "is-selected" : ""} ${isLocalUnsent ? "is-optimistic" : ""}`}>
      <span className={`ms-message-gesture ${Math.abs(swipeOffset) > 36 ? "is-visible" : ""}`} aria-hidden="true">↩</span>
      {showAvatar ? <UserAvatar person={message.sender} size="xs" className={`ms-message-avatar ${grouped ? "is-hidden" : ""}`} decorative /> : null}
      <div className={`ms-message-stack ${richOnly ? "ms-message-stack--rich-only" : ""} ${hasRichContent ? "has-rich-content" : ""}`} style={{ transform: `translateX(${swipeOffset}px)` }}>
        {showAuthor ? (
          <div className="ms-message-author">
            <strong>{own ? "You" : senderName}</strong>
            {isEncrypted ? <span>Encrypted</span> : null}
          </div>
        ) : null}

        <article
          className="ms-message-card"
          onContextMenu={(event) => {
            event.preventDefault();
            setShowContextMenu(true);
          }}
          onMouseLeave={() => {
            clearLongPress();
            setSwipeOffset(0);
          }}
          onTouchStart={handleTouchStart}
          onTouchMove={handleTouchMove}
          onTouchEnd={handleTouchEnd}
          onTouchCancel={() => {
            clearLongPress();
            resetSwipe();
          }}
        >
          <MessageActions
            message={message}
            own={own}
            canForward={canForward}
            open={showContextMenu}
            onOpen={() => setShowContextMenu(true)}
            onClose={() => setShowContextMenu(false)}
            onReact={handleReact}
            onReply={onReply}
            onForward={onForward}
            onEdit={onEdit}
            onDelete={onDelete}
            onReport={onReport}
            disabled={actionPending}
          />
          {callEvent ? <CallEventMessage event={callEvent} /> : null}
          <MediaMessage attachments={media} currentUserId={currentUserId} onPreviewAttachment={onPreviewAttachment} warmMedia={warmMedia} own={own} />

          {hasCopySurface ? (
            <div className="ms-message-copy">
              <ReplyPreview message={message} onJumpToReply={onJumpToReply} />
              {hasText ? (
                <MessageText
                  text={message.text || ""}
                  deleted={message.is_deleted}
                  encrypted={isEncrypted}
                  decryptionState={message.decryption_state}
                  decryptionMessage={message.decryption_message}
                  searchQuery={searchQuery}
                />
              ) : null}
              {message.transcript?.text ? <div className="ms-message-transcript">{message.transcript.text}</div> : null}
              {message.links?.length ? (
                <div className="ms-message-links">
                  {message.links.map((link) => <a key={link} href={link} target="_blank" rel="noreferrer">{link}</a>)}
                </div>
              ) : null}
              <MessageMeta
                message={message}
                own={own}
                receiptStatus={receiptStatus}
                receiptSummary={receiptSummary}
                onRetry={onRetry}
                actionError={actionError}
                actionPending={actionPending}
              />
            </div>
          ) : null}

          {message.voice_note?.is_voice_note && !audio.length ? (
            <div className="ms-voice-message-fallback">
              <strong>{reactedWithHeart ? "Voice note · liked" : "Voice note"}</strong>
              <span>{message.voice_note.duration_seconds ? `${message.voice_note.duration_seconds}s` : "Audio message"}</span>
            </div>
          ) : null}

          {audio.map((attachment) => {
            const src = getAttachmentPlaybackUrl(attachment);
            if (!src) return null;
            return (
              <AudioMessagePlayer
                key={attachment.id}
                src={src}
                label={message.voice_note?.is_voice_note ? "Voice note" : attachment.original_name}
                compact={Boolean(message.voice_note?.is_voice_note)}
                attachment={attachment}
                currentUserId={currentUserId}
                waveformData={message.voice_note?.waveform ?? (Array.isArray(attachment.metadata?.waveform) ? attachment.metadata.waveform.map(Number) : undefined)}
              />
            );
          })}

          <AttachmentMessage attachments={files} currentUserId={currentUserId} onPreviewAttachment={onPreviewAttachment} />

          {!message.is_deleted && !isFailed && !isLocalUnsent ? (
            <ReactionBar
              message={message}
              currentUserId={currentUserId}
              onToggle={(emoji) => onToggleReaction(message, emoji)}
            />
          ) : null}
        </article>

        {!hasCopySurface ? (
          <MessageMeta
            message={message}
            own={own}
            receiptStatus={receiptStatus}
            receiptSummary={receiptSummary}
            onRetry={onRetry}
            actionError={actionError}
            actionPending={actionPending}
          />
        ) : null}
      </div>
    </div>
  );
}
