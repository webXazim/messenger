import type { UserSearchResult } from "../types/auth";

export const GROUP_TITLE_MAX_LENGTH = 100;

export function normalizeGroupTitle(value: string) {
  return value.replace(/\s+/g, " ").trim();
}

export function isMeaningfulGroupTitle(value: string) {
  const normalized = normalizeGroupTitle(value);
  return normalized.length >= 2 && Array.from(normalized).some((character) => /[\p{L}\p{N}]/u.test(character));
}

export function validateGroupDraft(title: string, participantIds: string[]) {
  const normalizedTitle = normalizeGroupTitle(title);
  const uniqueParticipantIds = Array.from(new Set(participantIds.map(String).filter(Boolean)));
  const errors: { title?: string; participants?: string } = {};

  if (!normalizedTitle) errors.title = "Enter a group name.";
  else if (!isMeaningfulGroupTitle(normalizedTitle)) errors.title = "Use at least two letters or numbers in the group name.";
  else if (normalizedTitle.length > GROUP_TITLE_MAX_LENGTH) errors.title = `Keep the group name under ${GROUP_TITLE_MAX_LENGTH} characters.`;

  if (!uniqueParticipantIds.length) errors.participants = "Choose at least one member.";

  return {
    title: normalizedTitle,
    participantIds: uniqueParticipantIds,
    errors,
    valid: !errors.title && !errors.participants,
  };
}

export function dedupeUsers(users: UserSearchResult[], currentUserId?: string | null) {
  const seen = new Set<string>();
  return users.filter((person) => {
    const id = String(person.id || "");
    if (!id || id === String(currentUserId || "") || seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}
