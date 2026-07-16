import type { ReactNode } from "react";
import type { ConversationFilter } from "./types";

function SearchIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m15.5 15.5 5 5" /></svg>;
}

function ClearIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17" /></svg>;
}

const FILTERS: readonly { value: ConversationFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "unread", label: "Unread" },
  { value: "groups", label: "Groups" },
  { value: "archived", label: "Archived" },
];

export function ConversationListControls({
  search,
  filter,
  searchInputId,
  onSearchChange,
  onFilterChange,
  middleContent,
}: {
  search: string;
  filter: ConversationFilter;
  searchInputId?: string;
  onSearchChange: (value: string) => void;
  onFilterChange: (value: ConversationFilter) => void;
  middleContent?: ReactNode;
}) {
  return (
    <div className="ms-inbox-list__tools">
      <label className="ms-inbox-search">
        <span className="ms-inbox-search__icon"><SearchIcon /></span>
        <input
          id={searchInputId}
          type="search"
          placeholder="Search chats"
          aria-label="Search chats"
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
        />
        {search ? (
          <button type="button" onClick={() => onSearchChange("")} aria-label="Clear chat search">
            <ClearIcon />
          </button>
        ) : null}
      </label>

      {middleContent}

      <div className="ms-inbox-filters" role="group" aria-label="Chat filters">
        {FILTERS.map((item) => (
          <button
            key={item.value}
            type="button"
            aria-pressed={filter === item.value}
            onClick={() => onFilterChange(item.value)}
          >
            {item.label}
          </button>
        ))}
      </div>
    </div>
  );
}
