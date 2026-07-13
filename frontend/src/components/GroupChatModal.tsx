import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { UserSearchResult } from "../types/auth";
import { dedupeUsers, GROUP_TITLE_MAX_LENGTH, validateGroupDraft } from "../lib/groupUsability";
import { useModalAccessibility } from "../hooks/useModalAccessibility";
import { UserAvatar } from "./UserAvatar";
import { personPresenceText } from "../lib/personPresentation";

function userLabel(user: UserSearchResult) {
  return user.display_name || user.full_name || user.username;
}

export function GroupChatModal({
  friends,
  currentUserId,
  busy,
  error,
  onClose,
  onCreate,
}: {
  friends: UserSearchResult[];
  currentUserId?: string | null;
  busy?: boolean;
  error?: string | null;
  onClose: () => void;
  onCreate: (title: string, participantIds: string[]) => void;
}) {
  const [title, setTitle] = useState("");
  const [query, setQuery] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [fieldErrors, setFieldErrors] = useState<{ title?: string; participants?: string }>({});
  const titleInputRef = useRef<HTMLInputElement | null>(null);
  const titleId = useId();
  const descriptionId = useId();
  const submittingRef = useRef(false);
  const dialogRef = useModalAccessibility<HTMLElement>({
    onClose,
    initialFocusRef: titleInputRef,
    closeOnEscape: !busy,
  });

  useEffect(() => {
    if (!busy) submittingRef.current = false;
  }, [busy, error]);

  const uniqueFriends = useMemo(() => dedupeUsers(friends, currentUserId), [friends, currentUserId]);

  const filteredFriends = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return uniqueFriends;
    return uniqueFriends.filter((friend) => `${userLabel(friend)} ${friend.username}`.toLowerCase().includes(needle));
  }, [query, uniqueFriends]);

  const toggleFriend = (id: string) => {
    setFieldErrors((current) => ({ ...current, participants: undefined }));
    setSelectedIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  };

  const submit = () => {
    if (busy || submittingRef.current) return;
    const draft = validateGroupDraft(title, selectedIds);
    setFieldErrors(draft.errors);
    if (!draft.valid) return;
    submittingRef.current = true;
    onCreate(draft.title, draft.participantIds);
  };

  return (
    <div
      className="ms-modal-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
    >
      <section ref={dialogRef} className="ms-modal ms-group-modal" role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId} aria-busy={busy} tabIndex={-1}>
        <header className="ms-modal__header ms-modal__header--top">
          <div>
            <h3 id={titleId}>Create group</h3>
            <p id={descriptionId} className="ms-muted">Give the group a clear name and choose at least one person.</p>
          </div>
          <button type="button" className="ms-icon-button" disabled={busy} onClick={onClose} aria-label="Close create group dialog">×</button>
        </header>

        <label className="ms-field-stack">
          <span>Group name</span>
          <input
            ref={titleInputRef}
            value={title}
            maxLength={GROUP_TITLE_MAX_LENGTH}
            onChange={(event) => {
              setTitle(event.target.value);
              setFieldErrors((current) => ({ ...current, title: undefined }));
            }}
            placeholder="Project team"
            aria-invalid={Boolean(fieldErrors.title)}
            aria-describedby={fieldErrors.title ? "group-name-error" : "group-name-help"}
          />
          <small id="group-name-help" className="ms-muted">{title.length}/{GROUP_TITLE_MAX_LENGTH}</small>
          {fieldErrors.title ? <span id="group-name-error" className="ms-error-text">{fieldErrors.title}</span> : null}
        </label>

        <label className="ms-field-stack">
          <span>Search friends</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Name or username" />
        </label>

        <div className="ms-group-modal__selected-summary">
          <strong>{selectedIds.length} selected</strong>
          {selectedIds.length ? <button type="button" disabled={busy} onClick={() => setSelectedIds([])}>Clear</button> : null}
        </div>
        {fieldErrors.participants ? <div className="ms-error-text" role="alert">{fieldErrors.participants}</div> : null}
        {error ? <div className="ms-error-text" role="alert">{error}</div> : null}

        <div className="ms-member-picker" role="group" aria-label="Choose group members">
          {filteredFriends.map((friend) => {
            const selected = selectedIds.includes(String(friend.id));
            return (
              <button
                key={friend.id}
                type="button"
                className={`ms-member-picker__row ${selected ? "is-selected" : ""}`}
                aria-pressed={selected}
                disabled={busy}
                onClick={() => toggleFriend(String(friend.id))}
              >
                <UserAvatar person={friend} size="md" showPresence className="ms-member-picker__avatar" decorative />
                <span>
                  <strong>{userLabel(friend)}</strong>
                  <span className="ms-muted">@{friend.username} · {personPresenceText(friend)}</span>
                </span>
                <span className="ms-member-picker__action">{selected ? "Selected" : "Add"}</span>
              </button>
            );
          })}
          {!filteredFriends.length ? (
            <div className="ms-modal__empty">
              {uniqueFriends.length ? "No friends match this search." : "No friends are available yet."}
            </div>
          ) : null}
        </div>

        <div className="ms-button-row ms-modal__actions">
          <button type="button" className="ms-button ms-button--ghost" disabled={busy} onClick={onClose}>Cancel</button>
          <button type="button" className="ms-button ms-button--primary" disabled={busy} onClick={submit}>
            {busy ? "Creating…" : selectedIds.length ? `Create group with ${selectedIds.length}` : "Create group"}
          </button>
        </div>
      </section>
    </div>
  );
}
