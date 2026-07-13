import { NavLink } from "react-router-dom";
import { UserAvatar } from "../UserAvatar";
import type { Conversation, UserLite } from "../../types/chat";
import {
  conversationDisplayName,
  conversationPeer,
  conversationSnippet,
  conversationTime,
  conversationViewerParticipant,
} from "./conversationPresentation";

function LockIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="10" width="14" height="10" rx="2" /><path d="M8.5 10V7.5a3.5 3.5 0 0 1 7 0V10" /></svg>;
}

function SecurityIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 20 6v5c0 5-3.3 8.3-8 10-4.7-1.7-8-5-8-10V6l8-3Z" /><path d="M12 8v4M12 15.5h.01" /></svg>;
}

function PinIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 4 6 6M8 10l6 6M13.5 5.5l5 5-3 1.5-2.5 2.5L10.5 17l-1.5 3-1-5-4-4 3-1.5 2.5-2.5 1.5-3Z" /></svg>;
}

function MuteIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 9v6h4l5 4V5L9 9H5Z" /><path d="m18 9 4 4M22 9l-4 4" /></svg>;
}

function ArchiveIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16v13H4V7Zm-1-3h18v3H3V4Z" /><path d="M9 11h6" /></svg>;
}

export function ConversationRow({
  conversation,
  currentUserId,
  currentUser,
}: {
  conversation: Conversation;
  currentUserId?: string;
  currentUser?: Partial<UserLite> | null;
}) {
  const title = conversationDisplayName(conversation, currentUserId, currentUser);
  const unread = conversation.unread_count > 0;
  const peer = conversationPeer(conversation, currentUserId, currentUser);
  const viewer = conversationViewerParticipant(conversation, currentUserId, currentUser);
  const secure = Boolean(conversation.last_message?.is_encrypted);
  const securityUpdate = Boolean(conversation.e2ee_rekey_required);

  return (
    <NavLink
      to={`/chat/${conversation.id}`}
      className={({ isActive }) => `ms-inbox-row${isActive ? " is-active" : ""}${unread ? " has-unread" : ""}`}
    >
      <UserAvatar
        person={peer ?? { display_name: title }}
        size="md"
        shape={conversation.type === "group" ? "rounded" : "circle"}
        showPresence={conversation.type === "direct"}
        className={conversation.type === "group" ? "ms-inbox-avatar ms-inbox-avatar--group" : "ms-inbox-avatar"}
        decorative
      />

      <span className="ms-inbox-row__content">
        <span className="ms-inbox-row__topline">
          <span className="ms-inbox-row__title">
            <strong title={title}>{title}</strong>
            {viewer?.is_pinned ? <span className="ms-inbox-row__state" title="Pinned" aria-label="Pinned"><PinIcon /></span> : null}
            {viewer?.is_muted ? <span className="ms-inbox-row__state" title="Muted" aria-label="Muted"><MuteIcon /></span> : null}
            {viewer?.is_archived ? <span className="ms-inbox-row__state" title="Archived" aria-label="Archived"><ArchiveIcon /></span> : null}
            {secure ? <span className="ms-inbox-row__state" title="End-to-end encrypted" aria-label="End-to-end encrypted"><LockIcon /></span> : null}
            {securityUpdate ? <span className="ms-inbox-row__state ms-inbox-row__state--warning" title="Security update required" aria-label="Security update required"><SecurityIcon /></span> : null}
          </span>
          <time dateTime={conversation.last_message?.created_at || conversation.last_message_at || undefined}>{conversationTime(conversation)}</time>
        </span>
        <span className="ms-inbox-row__bottomline">
          <span className="ms-inbox-row__preview">{conversationSnippet(conversation, currentUserId, currentUser)}</span>
          {unread ? <span className="ms-inbox-row__badge" aria-label={`${conversation.unread_count} unread messages`}>{conversation.unread_count > 99 ? "99+" : conversation.unread_count}</span> : null}
        </span>
      </span>
    </NavLink>
  );
}
