import { useEffect, useState } from "react";
import { resolveMediaUrl } from "../lib/mediaUrl";
import { personDisplayName, personInitials, type PersonPresentation } from "../lib/personPresentation";

type AvatarSize = "xs" | "sm" | "md" | "lg" | "xl";

type UserAvatarProps = {
  person?: PersonPresentation | null;
  size?: AvatarSize;
  showPresence?: boolean;
  shape?: "circle" | "rounded";
  className?: string;
  decorative?: boolean;
};

export function PresenceBadge({ online, idle = false, label }: { online?: boolean | null; idle?: boolean; label?: string }) {
  return (
    <span
      className={`ms-presence-badge ${online ? idle ? "is-idle" : "is-online" : "is-offline"}`}
      aria-label={label || (online ? idle ? "Idle" : "Online" : "Offline")}
      title={label || (online ? idle ? "Idle" : "Online" : "Offline")}
    />
  );
}

export function UserAvatar({
  person,
  size = "md",
  showPresence = false,
  shape = "circle",
  className = "",
  decorative = false,
}: UserAvatarProps) {
  const imageUrl = resolveMediaUrl(person?.avatar);
  const [imageFailed, setImageFailed] = useState(false);
  const label = personDisplayName(person);

  useEffect(() => setImageFailed(false), [imageUrl]);

  return (
    <span
      className={`ms-user-avatar ms-user-avatar--${size} ms-user-avatar--${shape}${className ? ` ${className}` : ""}`}
      role={decorative ? undefined : "img"}
      aria-hidden={decorative || undefined}
      aria-label={decorative ? undefined : label}
    >
      {imageUrl && !imageFailed ? (
        <img src={imageUrl} alt="" onError={() => setImageFailed(true)} />
      ) : (
        <span className="ms-user-avatar__fallback" aria-hidden="true">{personInitials(person)}</span>
      )}
      {showPresence ? (
        <PresenceBadge
          online={Boolean(person?.is_online)}
          idle={person?.presence_status === "idle" || person?.presence_label === "idle"}
        />
      ) : null}
    </span>
  );
}
