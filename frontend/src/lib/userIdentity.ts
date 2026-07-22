type IdentityLike = {
  id?: string | number | null;
  username?: string | null;
  email?: string | null;
  display_name?: string | null;
};

function normalized(value: unknown) {
  return String(value ?? "").trim().toLowerCase();
}

export function isSameUserIdentity(a: IdentityLike | null | undefined, b: IdentityLike | null | undefined) {
  if (!a || !b) return false;
  const ids = [normalized(a.id), normalized(b.id)];
  // IDs are authoritative. Never fall through to a shared display name when
  // two fully identified users have different IDs; doing so reverses incoming
  // and outgoing notification/receipt handling for namesakes.
  if (ids[0] && ids[1]) return ids[0] === ids[1];
  const usernames = [normalized(a.username), normalized(b.username)];
  if (usernames[0] && usernames[1]) return usernames[0] === usernames[1];
  const emails = [normalized(a.email), normalized(b.email)];
  if (emails[0] && emails[1]) return emails[0] === emails[1];
  const displayNames = [normalized(a.display_name), normalized(b.display_name)];
  return Boolean(displayNames[0] && displayNames[1] && displayNames[0] === displayNames[1]);
}
