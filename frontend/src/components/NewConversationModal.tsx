import { useId, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { authApi } from "../api/auth";
import { useDebouncedValue } from "../hooks/useDebouncedValue";
import { useModalAccessibility } from "../hooks/useModalAccessibility";
import { UserAvatar } from "./UserAvatar";
import { personPresenceText } from "../lib/personPresentation";
import type { UserSearchResult } from "../types/auth";
import type { Conversation } from "../types/chat";

function displayName(person: UserSearchResult) {
  return person.display_name || person.full_name || [person.first_name, person.last_name].filter(Boolean).join(" ") || person.username;
}

function hasDirectConversation(conversations: Conversation[], personId: string) {
  return conversations.some(
    (conversation) => conversation.type === "direct" && conversation.participants.some((participant) => String(participant.user.id) === String(personId)),
  );
}

function SearchIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m15.5 15.5 5 5" /></svg>;
}

function CloseIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17" /></svg>;
}

export function NewConversationModal({
  contacts,
  conversations,
  currentUserId,
  busyUserId,
  error,
  onSelect,
  onClose,
}: {
  contacts: UserSearchResult[];
  conversations: Conversation[];
  currentUserId?: string;
  busyUserId?: string | null;
  error?: string | null;
  onSelect: (person: UserSearchResult) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query.trim(), 300);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useModalAccessibility<HTMLDivElement>({ onClose, initialFocusRef: inputRef });
  const searchQuery = useQuery({
    queryKey: ["user-search", "new-conversation", debouncedQuery],
    queryFn: ({ signal }) => authApi.searchUsers(debouncedQuery, signal),
    enabled: debouncedQuery.length >= 2,
    placeholderData: (previous) => previous,
    staleTime: 15_000,
  });

  const normalizedQuery = query.trim().toLowerCase();
  const people = useMemo(() => {
    const source = debouncedQuery.length >= 2
      ? (searchQuery.data ?? [])
      : contacts.filter((person) => {
          if (!normalizedQuery) return true;
          return [displayName(person), person.username].join(" ").toLowerCase().includes(normalizedQuery);
        });
    const seen = new Set<string>();
    return source.filter((person) => {
      const id = String(person.id || "");
      if (!id || id === String(currentUserId || "") || person.is_current_user || seen.has(id)) return false;
      seen.add(id);
      return true;
    });
  }, [contacts, currentUserId, debouncedQuery.length, normalizedQuery, searchQuery.data]);

  const searchSettling = query.trim() !== debouncedQuery || searchQuery.isFetching;
  const heading = debouncedQuery.length >= 2 ? "Search results" : "Contacts";
  const helperText = normalizedQuery.length === 1
    ? "Type one more character to search everyone."
    : searchSettling ? "Searching…" : "";

  return (
    <div className="ms-modal-backdrop ms-new-conversation-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <div ref={dialogRef} className="ms-modal ms-new-conversation-modal" role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId} tabIndex={-1}>
        <header className="ms-modal__header ms-modal__header--top">
          <div>
            <h3 id={titleId}>New conversation</h3>
            <p id={descriptionId} className="ms-muted">Choose a person and open the existing private chat, or start one.</p>
          </div>
          <button type="button" className="ms-icon-button" onClick={onClose} aria-label="Close new conversation">
            <CloseIcon />
          </button>
        </header>

        <label className="ms-new-conversation-search">
          <span aria-hidden="true"><SearchIcon /></span>
          <input
            ref={inputRef}
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search by name or username"
            aria-label="Search people"
          />
        </label>

        {error ? <div className="ms-page-error" role="alert">{error}</div> : null}
        {searchQuery.isError && debouncedQuery.length >= 2 ? (
          <div className="ms-modal__notice" role="alert">
            Search is unavailable right now. <button type="button" onClick={() => void searchQuery.refetch()}>Retry</button>
          </div>
        ) : null}

        <section className="ms-new-conversation-modal__results" aria-label={heading}>
          <div className="ms-new-conversation-modal__section-title">
            <strong>{heading}</strong>
            {helperText ? <span role="status">{helperText}</span> : null}
          </div>

          <div className="ms-new-conversation-modal__list" aria-busy={searchSettling}>
            {people.map((person) => {
              const existing = hasDirectConversation(conversations, person.id);
              const isBusy = String(busyUserId || "") === String(person.id);
              const name = displayName(person);
              return (
                <button
                  key={person.id}
                  type="button"
                  className="ms-new-conversation-person"
                  disabled={Boolean(busyUserId)}
                  onClick={() => onSelect(person)}
                >
                  <UserAvatar person={person} size="md" showPresence className="ms-new-conversation-person__avatar" decorative />
                  <span className="ms-new-conversation-person__copy">
                    <strong>{name}</strong>
                    <span>@{person.username} · {personPresenceText(person)}</span>
                  </span>
                  <span className="ms-new-conversation-person__action">{isBusy ? "Opening…" : existing ? "Open" : "Chat"}</span>
                </button>
              );
            })}

            {!searchSettling && !people.length ? (
              <div className="ms-modal__empty">
                {normalizedQuery
                  ? debouncedQuery.length >= 2
                    ? "No people match this search."
                    : "No contacts match yet. Type one more character to search everyone."
                  : "No contacts yet. Search by name or username to start a private conversation."}
              </div>
            ) : null}
          </div>
        </section>
      </div>
    </div>
  );
}
