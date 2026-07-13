import { useCallback, useEffect, useState } from "react";
import type { ConversationFilter } from "../components/conversations/types";

const STORAGE_KEY = "messenger:conversation-list:v1";
const VALID_FILTERS = new Set<ConversationFilter>(["all", "unread", "groups", "archived"]);

type StoredPreferences = {
  search?: string;
  filter?: ConversationFilter;
};

function readPreferences(): Required<StoredPreferences> {
  if (typeof window === "undefined") return { search: "", filter: "all" };
  try {
    const parsed = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) || "{}") as StoredPreferences;
    return {
      search: typeof parsed.search === "string" ? parsed.search.slice(0, 120) : "",
      filter: parsed.filter && VALID_FILTERS.has(parsed.filter) ? parsed.filter : "all",
    };
  } catch {
    return { search: "", filter: "all" };
  }
}

export function useConversationListPreferences() {
  const [preferences, setPreferences] = useState(readPreferences);

  useEffect(() => {
    try {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(preferences));
    } catch {
      // A private browsing mode can disable storage. The in-memory state remains usable.
    }
  }, [preferences]);

  const setSearch = useCallback((search: string) => {
    setPreferences((current) => ({ ...current, search: search.slice(0, 120) }));
  }, []);

  const setFilter = useCallback((filter: ConversationFilter) => {
    setPreferences((current) => ({ ...current, filter }));
  }, []);

  return {
    search: preferences.search,
    filter: preferences.filter,
    setSearch,
    setFilter,
  };
}
