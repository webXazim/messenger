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
  if (ids[0] && ids[1] && ids[0] === ids[1]) return true;
  const usernames = [normalized(a.username), normalized(b.username)];
  if (usernames[0] && usernames[1] && usernames[0] === usernames[1]) return true;
  const emails = [normalized(a.email), normalized(b.email)];
  if (emails[0] && emails[1] && emails[0] === emails[1]) return true;
  const displayNames = [normalized(a.display_name), normalized(b.display_name)];
  return Boolean(displayNames[0] && displayNames[1] && displayNames[0] === displayNames[1]);
}
