import { useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { chatApi } from "../../api/chat";
import { parseApiError } from "../../lib/apiErrors";
import { personDisplayName } from "../../lib/personPresentation";
import type { UserSearchResult } from "../../types/auth";
import type { UserLite, UserStatus } from "../../types/chat";
import { UserAvatar } from "../UserAvatar";

const STATUS_COLORS = ["#151515", "#5b21b6", "#0f766e", "#be123c", "#1d4ed8", "#a16207"] as const;
const OPEN_USER_STATUS_EVENT = "crescentsphere:open-user-status";

export function openUserStatus(userId: string) {
  window.dispatchEvent(new CustomEvent(OPEN_USER_STATUS_EVENT, { detail: { userId } }));
}

export function useUserStatuses() {
  return useQuery({
    queryKey: ["user-statuses"],
    queryFn: ({ signal }) => chatApi.listStatuses(signal),
    staleTime: 15_000,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  });
}

function PlusIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>;
}

function CloseIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg>;
}

function PhotoIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3" /><circle cx="9" cy="10" r="2" /><path d="m5 18 5-5 3 3 2-2 4 4" /></svg>;
}

function VideoIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="14" height="14" rx="3" /><path d="m17 10 4-2v8l-4-2Z" /></svg>;
}

function SoundIcon({ muted }: { muted: boolean }) {
  return muted
    ? <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 10v4h4l5 4V6L9 10H5Z" /><path d="m18 9 4 6M22 9l-4 6" /></svg>
    : <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 10v4h4l5 4V6L9 10H5Z" /><path d="M17 9a5 5 0 0 1 0 6M19 6a9 9 0 0 1 0 12" /></svg>;
}

function timeAgo(value: string) {
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return "now";
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (seconds < 60) return "now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h`;
}

type StatusGroup = { author: UserLite; statuses: UserStatus[]; latestAt: number };

function groupStatuses(statuses: UserStatus[], currentUserId: string) {
  const groups = new Map<string, StatusGroup>();
  for (const status of statuses) {
    const id = String(status.author.id || "");
    if (!id) continue;
    const existing = groups.get(id) ?? { author: status.author, statuses: [], latestAt: 0 };
    existing.statuses.push(status);
    existing.latestAt = Math.max(existing.latestAt, new Date(status.created_at).getTime() || 0);
    groups.set(id, existing);
  }
  return [...groups.values()]
    .map((group) => ({ ...group, statuses: group.statuses.sort((a, b) => a.created_at.localeCompare(b.created_at)) }))
    .sort((a, b) => {
      if (String(a.author.id) === currentUserId) return -1;
      if (String(b.author.id) === currentUserId) return 1;
      const aUnseen = a.statuses.some((item) => !item.is_viewed);
      const bUnseen = b.statuses.some((item) => !item.is_viewed);
      if (aUnseen !== bUnseen) return aUnseen ? -1 : 1;
      return b.latestAt - a.latestAt;
    });
}

function StatusComposer({ onClose, onPublished }: { onClose: () => void; onPublished: (status: UserStatus) => void }) {
  const [mode, setMode] = useState<"text" | "media">("text");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [backgroundColor, setBackgroundColor] = useState<string>(STATUS_COLORS[0]);
  const [progress, setProgress] = useState(0);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const previewUrl = useMemo(() => file ? URL.createObjectURL(file) : "", [file]);

  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl); }, [previewUrl]);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const publishMutation = useMutation({
    mutationFn: async () => {
      let uploadId: string | undefined;
      if (file) {
        const upload = await chatApi.uploadFile(file, {
          original_name: file.name,
          mime_type: file.type,
          onProgress: setProgress,
        });
        uploadId = upload.id;
      }
      return chatApi.createStatus({
        text: text.trim(),
        upload_id: uploadId,
        background_color: backgroundColor,
        text_color: "#ffffff",
      });
    },
    onSuccess: onPublished,
  });

  const chooseFile = (event: ChangeEvent<HTMLInputElement>) => {
    const selected = event.target.files?.[0] ?? null;
    event.target.value = "";
    if (!selected) return;
    if (!selected.type.startsWith("image/") && !selected.type.startsWith("video/")) return;
    setFile(selected);
    setMode("media");
    setProgress(0);
  };
  const canPublish = Boolean(file || text.trim()) && !publishMutation.isPending;
  const error = publishMutation.error ? parseApiError(publishMutation.error, "Story could not be shared.").message : "";

  return createPortal(
    <div className="ms-status-modal" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !publishMutation.isPending) onClose(); }}>
      <section className="ms-status-composer" role="dialog" aria-modal="true" aria-labelledby="status-composer-title">
        <header>
          <div><span>Visible for 24 hours</span><h2 id="status-composer-title">Create story</h2></div>
          <button type="button" className="ms-status-icon-button" onClick={onClose} disabled={publishMutation.isPending} aria-label="Close story composer"><CloseIcon /></button>
        </header>

        <div className="ms-status-composer__modes" role="tablist" aria-label="Story type">
          <button type="button" role="tab" aria-selected={mode === "text"} onClick={() => { setMode("text"); setFile(null); }}><span>Aa</span>Text</button>
          <button type="button" role="tab" aria-selected={mode === "media"} onClick={() => fileInputRef.current?.click()}><PhotoIcon />Photo or video</button>
        </div>

        <input ref={fileInputRef} type="file" accept="image/*,video/*" onChange={chooseFile} hidden />
        {mode === "media" && file ? (
          <div className="ms-status-composer__media">
            {file.type.startsWith("video/") ? <video src={previewUrl} controls playsInline /> : <img src={previewUrl} alt="Selected status preview" />}
            <button type="button" onClick={() => fileInputRef.current?.click()}>Change</button>
          </div>
        ) : (
          <div className="ms-status-composer__text-preview" style={{ backgroundColor, color: "#ffffff" }}>
            <textarea autoFocus value={text} maxLength={800} onChange={(event) => setText(event.target.value)} placeholder="What's on your mind?" aria-label="Story text" />
          </div>
        )}

        {mode === "media" && file ? (
          <label className="ms-status-composer__caption"><span>Caption (optional)</span><textarea value={text} maxLength={500} onChange={(event) => setText(event.target.value)} placeholder="Add a caption…" /></label>
        ) : (
          <div className="ms-status-palette" role="group" aria-label="Background color">
            {STATUS_COLORS.map((color) => <button key={color} type="button" aria-label={`Use ${color} background`} aria-pressed={backgroundColor === color} style={{ backgroundColor: color }} onClick={() => setBackgroundColor(color)} />)}
          </div>
        )}

        {error ? <p className="ms-status-composer__error" role="alert">{error}</p> : null}
        {publishMutation.isPending && file ? <div className="ms-status-composer__progress" role="progressbar" aria-valuenow={Math.round(progress)}><span style={{ width: `${Math.max(4, progress)}%` }} /></div> : null}
        <footer>
          <span>Visible to your friends for 24 hours</span>
          <button type="button" className="ms-button ms-button--primary" disabled={!canPublish} onClick={() => publishMutation.mutate()}>{publishMutation.isPending ? "Sharing…" : "Share story"}</button>
        </footer>
      </section>
    </div>,
    document.body,
  );
}

function StatusViewer({ groups, groupId, initialIndex, currentUserId, onClose, onViewed, onDeleted, onMessage }: {
  groups: StatusGroup[];
  groupId: string;
  initialIndex: number;
  currentUserId: string;
  onClose: () => void;
  onViewed: (status: UserStatus) => void;
  onDeleted: (status: UserStatus) => void;
  onMessage?: (person: UserLite) => void;
}) {
  const [currentGroupId, setCurrentGroupId] = useState(groupId);
  const [index, setIndex] = useState(initialIndex);
  const [progress, setProgress] = useState(0);
  const [muted, setMuted] = useState(true);
  const groupIndex = groups.findIndex((item) => String(item.author.id) === currentGroupId);
  const group = groups[Math.max(0, groupIndex)];
  const status = group?.statuses[Math.min(index, Math.max(0, group.statuses.length - 1))];
  const isOwn = String(status?.author.id || "") === currentUserId;

  const move = (direction: -1 | 1) => {
    if (!group || !status) return;
    const nextIndex = index + direction;
    if (nextIndex >= 0 && nextIndex < group.statuses.length) {
      setIndex(nextIndex);
      return;
    }
    const nextGroupIndex = groupIndex + direction;
    if (nextGroupIndex < 0 || nextGroupIndex >= groups.length) {
      onClose();
      return;
    }
    const nextGroup = groups[nextGroupIndex];
    setCurrentGroupId(String(nextGroup.author.id));
    setIndex(direction > 0 ? 0 : Math.max(0, nextGroup.statuses.length - 1));
  };

  useEffect(() => {
    if (!status) return;
    setProgress(0);
    if (!status.is_viewed && !status.is_own) onViewed(status);
    const videoSeconds = Number(status.media?.duration_seconds || 0);
    const duration = status.content_type === "video" ? Math.min(Math.max(videoSeconds || 15, 4), 60) * 1000 : 6500;
    const startedAt = performance.now();
    const timer = window.setInterval(() => {
      const next = Math.min(1, (performance.now() - startedAt) / duration);
      setProgress(next);
      if (next >= 1) {
        window.clearInterval(timer);
        move(1);
      }
    }, 60);
    return () => window.clearInterval(timer);
    // move intentionally follows the active status snapshot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.id]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "ArrowRight") move(1);
      if (event.key === "ArrowLeft") move(-1);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  });

  if (!status || !group) return null;
  return createPortal(
    <div className="ms-status-viewer" role="dialog" aria-modal="true" aria-label={`${personDisplayName(group.author)}'s story`}>
      <div className="ms-status-viewer__backdrop" style={status.content_type === "text" ? { backgroundColor: status.background_color } : undefined} />
      <div className="ms-status-viewer__stage" style={status.content_type === "text" ? { backgroundColor: status.background_color, color: status.text_color } : undefined}>
        {status.content_type === "image" && status.media?.preview_url ? <img src={status.media.preview_url} alt={status.text || `${personDisplayName(group.author)}'s story`} /> : null}
        {status.content_type === "video" && status.media?.preview_url ? <video key={status.id} src={status.media.preview_url} autoPlay muted={muted} playsInline onEnded={() => move(1)} /> : null}
        {status.content_type === "text" ? <p>{status.text}</p> : status.text ? <div className="ms-status-viewer__caption">{status.text}</div> : null}
      </div>

      <header className="ms-status-viewer__header">
        <div className="ms-status-viewer__segments" aria-hidden="true">
          {group.statuses.map((item, itemIndex) => <span key={item.id}><i style={{ width: `${itemIndex < index ? 100 : itemIndex === index ? progress * 100 : 0}%` }} /></span>)}
        </div>
        {!isOwn && onMessage ? (
          <button type="button" className="ms-status-viewer__identity ms-status-viewer__identity--link" onClick={() => { onClose(); onMessage(group.author); }} aria-label={`Open chat with ${personDisplayName(group.author)}`}>
            <UserAvatar person={group.author} size="sm" decorative />
            <strong>{personDisplayName(group.author)}</strong>
            <span>{timeAgo(status.created_at)}</span>
          </button>
        ) : (
          <div className="ms-status-viewer__identity">
            <UserAvatar person={group.author} size="sm" decorative />
            <strong>Your story</strong>
            <span>{timeAgo(status.created_at)}</span>
          </div>
        )}
        <div className="ms-status-viewer__actions">
          {status.content_type === "video" ? <button type="button" onClick={() => setMuted((value) => !value)} aria-label={muted ? "Unmute story" : "Mute story"}><SoundIcon muted={muted} /></button> : null}
          <button type="button" onClick={onClose} aria-label="Close story"><CloseIcon /></button>
        </div>
      </header>

      <button type="button" className="ms-status-viewer__nav ms-status-viewer__nav--previous" onClick={() => move(-1)} aria-label="Previous status" />
      <button type="button" className="ms-status-viewer__nav ms-status-viewer__nav--next" onClick={() => move(1)} aria-label="Next status" />
      <footer className="ms-status-viewer__footer">
        {isOwn ? (
          <><span>{status.view_count} {status.view_count === 1 ? "view" : "views"}</span><button type="button" onClick={() => onDeleted(status)}>Delete</button></>
        ) : onMessage ? (
          <button type="button" className="ms-status-viewer__message" onClick={() => { onClose(); onMessage(group.author); }}>Message {personDisplayName(group.author)}</button>
        ) : null}
      </footer>
    </div>,
    document.body,
  );
}

export function StatusTray({ currentUser, friends = [], statuses = [], statusesLoading = false, busyUserId, onOpenFriend }: {
  currentUser?: Partial<UserLite> | null;
  friends?: UserSearchResult[];
  statuses?: UserStatus[];
  statusesLoading?: boolean;
  busyUserId?: string | null;
  onOpenFriend?: (friend: UserSearchResult) => void;
}) {
  const queryClient = useQueryClient();
  const currentUserId = String(currentUser?.id || "");
  const [composerOpen, setComposerOpen] = useState(false);
  const [viewer, setViewer] = useState<{ groupId: string; index: number } | null>(null);
  const groups = useMemo(() => groupStatuses(statuses, currentUserId), [currentUserId, statuses]);
  const groupIds = useMemo(() => new Set(groups.map((group) => String(group.author.id))), [groups]);
  const ownGroup = groups.find((group) => String(group.author.id) === currentUserId);
  const friendById = useMemo(() => new Map(friends.map((friend) => [String(friend.id), friend])), [friends]);
  const onlineWithoutStatus = useMemo(() => friends
    .filter((friend) => friend.is_online && !groupIds.has(String(friend.id)))
    .sort((a, b) => personDisplayName(a).localeCompare(personDisplayName(b))), [friends, groupIds]);

  const markViewed = (status: UserStatus) => {
    queryClient.setQueryData<UserStatus[]>(["user-statuses"], (current = []) => current.map((item) => item.id === status.id ? { ...item, is_viewed: true } : item));
    void chatApi.markStatusViewed(status.id).catch(() => {
      void queryClient.invalidateQueries({ queryKey: ["user-statuses"] });
    });
  };
  const deleteMutation = useMutation({
    mutationFn: chatApi.deleteStatus,
    onSuccess: (_, statusId) => {
      queryClient.setQueryData<UserStatus[]>(["user-statuses"], (current = []) => current.filter((item) => item.id !== statusId));
      setViewer(null);
    },
  });

  const openOwn = () => {
    if (ownGroup?.statuses.length) setViewer({ groupId: currentUserId, index: 0 });
    else setComposerOpen(true);
  };
  const openMessage = (person: UserLite) => {
    const friend = friendById.get(String(person.id));
    if (friend) onOpenFriend?.(friend);
  };

  useEffect(() => {
    const openRequestedStatus = (event: Event) => {
      const userId = String((event as CustomEvent<{ userId?: string }>).detail?.userId || "");
      const requestedGroup = groups.find((group) => String(group.author.id) === userId);
      if (!requestedGroup) return;
      const unseenIndex = requestedGroup.statuses.findIndex((item) => !item.is_viewed);
      setViewer({ groupId: userId, index: unseenIndex >= 0 ? unseenIndex : 0 });
    };
    window.addEventListener(OPEN_USER_STATUS_EVENT, openRequestedStatus);
    return () => window.removeEventListener(OPEN_USER_STATUS_EVENT, openRequestedStatus);
  }, [groups]);

  return (
    <>
      <section className="ms-status-tray" aria-label="Stories">
        <div className="ms-status-tray__heading"><strong>Stories</strong>{statusesLoading && !statuses.length ? <span>Loading…</span> : <span>24h</span>}</div>
        <div className="ms-status-tray__scroll ms-scroll-region" role="list">
          <div className="ms-status-tray__item ms-status-tray__item--own" role="listitem">
            <button type="button" className={`ms-status-tray__person${ownGroup ? " has-status" : ""}`} onClick={openOwn} aria-label={ownGroup ? "View your story" : "Add your story"}>
              <span className="ms-status-tray__ring"><UserAvatar person={currentUser} size="md" decorative /></span>
              <span>{ownGroup ? "Your story" : "Add story"}</span>
            </button>
            <button type="button" className="ms-status-tray__add" onClick={() => setComposerOpen(true)} aria-label="Create a new story"><PlusIcon /></button>
          </div>

          {groups.filter((group) => String(group.author.id) !== currentUserId).map((group) => {
            const unseen = group.statuses.some((item) => !item.is_viewed);
            const id = String(group.author.id);
            return (
              <button key={id} type="button" role="listitem" className={`ms-status-tray__person has-status${unseen ? " has-unseen" : ""}`} onClick={() => setViewer({ groupId: id, index: unseen ? Math.max(0, group.statuses.findIndex((item) => !item.is_viewed)) : 0 })}>
                <span className="ms-status-tray__ring"><UserAvatar person={group.author} size="md" showPresence decorative /></span>
                <span>{personDisplayName(group.author)}</span>
              </button>
            );
          })}

          {onlineWithoutStatus.map((friend) => (
            <button key={friend.id} type="button" role="listitem" className="ms-status-tray__person" disabled={Boolean(busyUserId)} onClick={() => onOpenFriend?.(friend)} aria-label={`Message ${personDisplayName(friend)}`}>
              <span className="ms-status-tray__ring"><UserAvatar person={friend} size="md" showPresence decorative /></span>
              <span>{busyUserId === String(friend.id) ? "Opening…" : personDisplayName(friend)}</span>
            </button>
          ))}
        </div>
      </section>

      {composerOpen ? <StatusComposer onClose={() => setComposerOpen(false)} onPublished={(status) => {
        queryClient.setQueryData<UserStatus[]>(["user-statuses"], (current = []) => [...current.filter((item) => item.id !== status.id), status]);
        setComposerOpen(false);
        setViewer({ groupId: currentUserId, index: ownGroup?.statuses.length ?? 0 });
      }} /> : null}
      {viewer ? <StatusViewer groups={groups} groupId={viewer.groupId} initialIndex={viewer.index} currentUserId={currentUserId} onClose={() => setViewer(null)} onViewed={markViewed} onDeleted={(status) => deleteMutation.mutate(status.id)} onMessage={onOpenFriend ? openMessage : undefined} /> : null}
    </>
  );
}
