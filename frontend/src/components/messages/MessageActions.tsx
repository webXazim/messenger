import { useEffect, useId, useRef, useState, type ReactElement } from "react";
import { createPortal } from "react-dom";
import type { Message } from "../../types/chat";

const QUICK_REACTIONS = ["👍", "❤️", "😂", "😮", "😢", "🔥"];

function ActionMenuLayer({ mobile, children }: { mobile: boolean; children: ReactElement }) {
  return mobile ? createPortal(children, document.body) : children;
}

function ReplyIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 8-5 4 5 4v-3h4.5c3.2 0 5.5 1.5 6.5 4.5-.2-5.2-2.8-8.5-7.5-8.5H9V8Z" /></svg>;
}

function MoreIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.4" /><circle cx="12" cy="12" r="1.4" /><circle cx="19" cy="12" r="1.4" /></svg>;
}

export function MessageActions({
  message,
  own,
  canForward,
  open,
  onOpen,
  onClose,
  onReact,
  onReply,
  onForward,
  onEdit,
  onDelete,
  onRestore,
  onReport,
  disabled = false,
}: {
  message: Message;
  own: boolean;
  canForward: boolean;
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
  onReact: (emoji: string) => void;
  onReply: (message: Message) => void;
  onForward: (message: Message) => void;
  onEdit: (message: Message) => void;
  onDelete: (message: Message) => void;
  onRestore: (message: Message) => void;
  onReport?: (message: Message) => void;
  disabled?: boolean;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const menuId = useId();
  const [mobileMenu, setMobileMenu] = useState(false);
  const [mobileMenuTop, setMobileMenuTop] = useState(72);
  const failed = String(message.delivery_status || "").toLowerCase() === "failed";
  const localUnsent = message.id.startsWith("temp-");
  const canInteract = !message.is_deleted && !failed && !localUnsent;
  const editDeadlineActive = !message.edit_deadline || Date.parse(message.edit_deadline) > Date.now();
  const canEdit = message.can_edit !== false && editDeadlineActive && (message.reactions?.length ?? 0) === 0;

  useEffect(() => {
    const media = window.matchMedia("(max-width: 720px)");
    const sync = () => setMobileMenu(media.matches);
    sync();
    media.addEventListener?.("change", sync);
    return () => media.removeEventListener?.("change", sync);
  }, []);

  useEffect(() => {
    if (!open) return;
    const frame = window.requestAnimationFrame(() => {
      if (mobileMenu) {
        const card = rootRef.current?.closest(".ms-message-card") as HTMLElement | null;
        const rect = (card ?? rootRef.current)?.getBoundingClientRect();
        if (rect) {
          const preferred = rect.top > 150 ? rect.top - 64 : rect.bottom + 8;
          setMobileMenuTop(Math.max(72, Math.min(preferred, window.innerHeight - 240)));
        }
      }
      menuRef.current?.querySelector<HTMLButtonElement>("button:not([disabled])")?.focus();
    });
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!rootRef.current?.contains(target) && !menuRef.current?.contains(target)) onClose();
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("pointerdown", handlePointerDown, true);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("pointerdown", handlePointerDown, true);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [mobileMenu, onClose, open]);

  const run = (action: () => void) => {
    onClose();
    action();
  };

  return (
    <div ref={rootRef} className={`ms-message-actions ${open ? "is-open" : ""}`}>
      {canInteract ? (
        <button type="button" className="ms-message-actions__button" disabled={disabled} onClick={() => onReply(message)} aria-label="Reply">
          <ReplyIcon />
        </button>
      ) : null}
      <button
        ref={triggerRef}
        type="button"
        className="ms-message-actions__button"
        onClick={open ? onClose : onOpen}
        disabled={disabled}
        aria-label="More message actions"
        aria-expanded={open}
        aria-haspopup="menu"
        aria-controls={open ? menuId : undefined}
      >
        <MoreIcon />
      </button>

      {open ? (
        <ActionMenuLayer mobile={mobileMenu}>
          <div
          ref={menuRef}
          id={menuId}
          className={`ms-message-actions__menu ${mobileMenu ? "is-mobile" : ""}`}
          style={mobileMenu ? { top: mobileMenuTop } : undefined}
          role="menu"
          aria-label="Message actions"
          onKeyDown={(event) => {
            const items = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>("button:not([disabled])") ?? []);
            const currentIndex = items.indexOf(document.activeElement as HTMLButtonElement);
            if (event.key === "Escape") {
              event.preventDefault();
              onClose();
              window.requestAnimationFrame(() => triggerRef.current?.focus());
              return;
            }
            if (event.key === "Tab") {
              onClose();
              return;
            }
            let nextIndex = currentIndex;
            if (event.key === "ArrowDown" || event.key === "ArrowRight") nextIndex = currentIndex < 0 ? 0 : (currentIndex + 1) % items.length;
            else if (event.key === "ArrowUp" || event.key === "ArrowLeft") nextIndex = currentIndex < 0 ? items.length - 1 : (currentIndex - 1 + items.length) % items.length;
            else if (event.key === "Home") nextIndex = 0;
            else if (event.key === "End") nextIndex = items.length - 1;
            else return;
            event.preventDefault();
            items[nextIndex]?.focus();
          }}
        >
          {canInteract ? (
            <div className="ms-message-actions__reactions" role="group" aria-label="React to message">
              {QUICK_REACTIONS.map((emoji) => (
                <button key={emoji} type="button" disabled={disabled} onClick={() => run(() => onReact(emoji))} aria-label={`React with ${emoji}`}>
                  {emoji}
                </button>
              ))}
            </div>
          ) : null}
          <div className="ms-message-actions__items">
            {canInteract ? <button type="button" role="menuitem" disabled={disabled} onClick={() => run(() => onReply(message))}>Reply</button> : null}
            {canInteract && message.text ? <button type="button" role="menuitem" disabled={disabled} onClick={() => run(() => { void navigator.clipboard.writeText(message.text); })}>Copy</button> : null}
            {canForward ? <button type="button" role="menuitem" disabled={disabled} onClick={() => run(() => onForward(message))}>Forward</button> : null}
            {own && canInteract && canEdit ? <button type="button" role="menuitem" disabled={disabled} onClick={() => run(() => onEdit(message))}>Edit</button> : null}
            {own && !message.is_deleted ? <button type="button" role="menuitem" className="is-danger" disabled={disabled} onClick={() => run(() => onDelete(message))}>Delete</button> : null}
            {own && message.is_deleted && message.can_restore !== false ? <button type="button" role="menuitem" disabled={disabled} onClick={() => run(() => onRestore(message))}>Restore</button> : null}
            {!own && onReport ? <button type="button" role="menuitem" className="is-danger" disabled={disabled} onClick={() => run(() => onReport(message))}>Report</button> : null}
          </div>
          </div>
        </ActionMenuLayer>
      ) : null}
    </div>
  );
}
