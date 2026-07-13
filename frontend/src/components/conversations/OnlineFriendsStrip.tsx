import { useMemo } from "react";
import { UserAvatar } from "../UserAvatar";
import { personDisplayName } from "../../lib/personPresentation";
import type { UserSearchResult } from "../../types/auth";

export function OnlineFriendsStrip({
  friends = [],
  busyUserId,
  onOpenFriend,
}: {
  friends?: UserSearchResult[];
  busyUserId?: string | null;
  onOpenFriend?: (friend: UserSearchResult) => void;
}) {
  const onlineFriends = useMemo(
    () => friends
      .filter((friend) => Boolean(friend.is_online))
      .sort((a, b) => personDisplayName(a).localeCompare(personDisplayName(b))),
    [friends],
  );

  if (!onlineFriends.length || !onOpenFriend) return null;

  return (
    <section className="ms-online-friends" aria-label="Friends online now">
      <div className="ms-online-friends__heading">
        <strong>Active now</strong>
        <span>{onlineFriends.length}</span>
      </div>
      <div className="ms-online-friends__scroll ms-scroll-region" role="list">
        {onlineFriends.map((friend) => {
          const id = String(friend.id);
          const busy = busyUserId === id;
          const label = personDisplayName(friend);
          return (
            <button
              key={id}
              type="button"
              role="listitem"
              className="ms-online-friends__person"
              disabled={Boolean(busyUserId)}
              onClick={() => onOpenFriend(friend)}
              aria-label={`Message ${label}`}
              title={`Message ${label}`}
            >
              <UserAvatar person={friend} size="md" showPresence decorative />
              <span>{busy ? "Opening…" : label}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
