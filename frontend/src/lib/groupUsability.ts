import type { UserSearchResult } from "../types/auth";

export const GROUP_TITLE_MAX_LENGTH = 100;
export const GROUP_UNIQUE_NAME_MAX_LENGTH = 80;

export function normalizeGroupUniqueName(value: string) {
  return value.toLocaleLowerCase().normalize("NFKC").replace(/[\s_]+/gu, "-").replace(/[^\p{L}\p{N}-]+/gu, "").replace(/-+/g, "-").replace(/^-|-$/g, "").slice(0, GROUP_UNIQUE_NAME_MAX_LENGTH);
}

export function groupUniqueNameError(value: string) {
  const normalized = normalizeGroupUniqueName(value);
  if (normalized.length < 3) return "Use at least three letters or numbers.";
  return "";
}

export function normalizeGroupTitle(value: string) {
  return value.replace(/\s+/g, " ").trim();
}

export function isMeaningfulGroupTitle(value: string) {
  const normalized = normalizeGroupTitle(value);
  return normalized.length >= 2 && Array.from(normalized).some((character) => /[\p{L}\p{N}]/u.test(character));
}

export function validateGroupDraft(title: string, uniqueName: string, participantIds: string[]) {
  const normalizedTitle = normalizeGroupTitle(title);
  const normalizedUniqueName = normalizeGroupUniqueName(uniqueName);
  const uniqueParticipantIds = Array.from(new Set(participantIds.map(String).filter(Boolean)));
  const errors: { title?: string; slug?: string; participants?: string } = {};

  if (!normalizedTitle) errors.title = "Enter a group name.";
  else if (!isMeaningfulGroupTitle(normalizedTitle)) errors.title = "Use at least two letters or numbers in the group name.";
  else if (normalizedTitle.length > GROUP_TITLE_MAX_LENGTH) errors.title = `Keep the group name under ${GROUP_TITLE_MAX_LENGTH} characters.`;
  errors.slug = groupUniqueNameError(normalizedUniqueName) || undefined;

  if (!uniqueParticipantIds.length) errors.participants = "Choose at least one member.";

  return {
    title: normalizedTitle,
    uniqueName: normalizedUniqueName,
    participantIds: uniqueParticipantIds,
    errors,
    valid: !errors.title && !errors.slug && !errors.participants,
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
