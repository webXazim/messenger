import { NavLink } from "react-router-dom";
import type { ReactNode } from "react";
import { APP_NAME } from "../../lib/config";
import { UserAvatar } from "../UserAvatar";

type NavigationItem = {
  to: string;
  label: string;
  icon: ReactNode;
};

type NavigationProps = {
  userLabel: string;
  userAvatar?: string | null;
  socketStatus: string;
  onLogout: () => void;
};

function ChatsIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M6 7.5h12a3.5 3.5 0 0 1 3.5 3.5v5A3.5 3.5 0 0 1 18 19.5H9l-4.5 3v-3A3.5 3.5 0 0 1 1 16v-5A3.5 3.5 0 0 1 4.5 7.5H6Z" />
    </svg>
  );
}

function CallsIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M7.7 4.5h2.6c.4 0 .8.3.9.7l.6 2.7a1 1 0 0 1-.3 1l-1.8 1.5a13.2 13.2 0 0 0 3.4 3.4l1.5-1.8a1 1 0 0 1 1-.3l2.7.6c.4.1.7.5.7.9v2.6c0 .6-.4 1-.9 1.1-.7.1-1.3.2-2 .2-7 0-12.6-5.6-12.6-12.6 0-.7.1-1.3.2-2 .1-.5.5-.9 1.1-.9Z" />
    </svg>
  );
}

function ContactsIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm7 1.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Zm-7 2c-3 0-5.5 1.6-5.5 3.5v.5h11v-.5c0-1.9-2.5-3.5-5.5-3.5Zm7 .5c-1 0-1.9.2-2.7.6 1 .7 1.7 1.7 1.7 2.9h5v-.4c0-1.7-1.8-3.1-4-3.1Z" />
    </svg>
  );
}

function GroupsIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M8 12.5a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm8-1a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5ZM3.5 19c0-2.2 2-4 4.5-4s4.5 1.8 4.5 4v.5h-9V19Zm9-1.2c.6-1.6 2-2.8 3.9-2.8 2.3 0 4.1 1.6 4.1 3.6v.4h-6.1" />
    </svg>
  );
}

function SettingsIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Zm8 3.5-.8-.4a7.7 7.7 0 0 0-.4-1l.4-.8a1 1 0 0 0-.2-1.1l-1.3-1.3a1 1 0 0 0-1.1-.2l-.8.4c-.3-.2-.7-.3-1-.4L14 4a1 1 0 0 0-1-.8h-2a1 1 0 0 0-1 .8l-.3.9c-.3.1-.7.2-1 .4l-.8-.4a1 1 0 0 0-1.1.2L5.5 6.4a1 1 0 0 0-.2 1.1l.4.8c-.2.3-.3.7-.4 1l-.9.3a1 1 0 0 0-.8 1v2a1 1 0 0 0 .8 1l.9.3c.1.3.2.7.4 1l-.4.8a1 1 0 0 0 .2 1.1l1.3 1.3a1 1 0 0 0 1.1.2l.8-.4c.3.2.7.3 1 .4l.3.9a1 1 0 0 0 1 .8h2a1 1 0 0 0 1-.8l.3-.9c.3-.1.7-.2 1-.4l.8.4a1 1 0 0 0 1.1-.2l1.3-1.3a1 1 0 0 0 .2-1.1l-.4-.8c.2-.3.3-.7.4-1l.9-.3a1 1 0 0 0 .8-1v-2a1 1 0 0 0-.8-1Z" />
    </svg>
  );
}

function LogoutIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M10 5H5.5A1.5 1.5 0 0 0 4 6.5v11A1.5 1.5 0 0 0 5.5 19H10M14 8l4 4-4 4M8 12h10" />
    </svg>
  );
}

export const MESSENGER_NAV_ITEMS: readonly NavigationItem[] = [
  { to: "/chat", label: "Chats", icon: <ChatsIcon /> },
  { to: "/calls", label: "Calls", icon: <CallsIcon /> },
  { to: "/friends", label: "Contacts", icon: <ContactsIcon /> },
  { to: "/groups", label: "Groups", icon: <GroupsIcon /> },
  { to: "/settings", label: "Settings", icon: <SettingsIcon /> },
] as const;

function BrandMark() {
  return (
    <div className="ms-navigation__brand-mark" aria-hidden="true">
      <span>C</span>
      <span>S</span>
    </div>
  );
}

function NavigationLinks({ mobile = false }: { mobile?: boolean }) {
  return (
    <nav
      className={mobile ? "ms-mobile-nav__links" : "ms-desktop-rail__links"}
      aria-label={mobile ? "Mobile navigation" : "Primary navigation"}
    >
      {MESSENGER_NAV_ITEMS.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          className={({ isActive }) =>
            `${mobile ? "ms-mobile-nav__link" : "ms-desktop-rail__link"}${isActive ? " is-active" : ""}`
          }
          aria-label={item.label}
        >
          <span className="ms-navigation__icon" aria-hidden="true">{item.icon}</span>
          <span className="ms-navigation__label">{item.label}</span>
        </NavLink>
      ))}
    </nav>
  );
}

export function DesktopNavigationRail({ userLabel, userAvatar, socketStatus, onLogout }: NavigationProps) {

  return (
    <aside className="ms-desktop-rail" aria-label={`${APP_NAME} navigation`}>
      <NavLink to="/chat" className="ms-desktop-rail__brand" aria-label={`${APP_NAME} chats`}>
        <BrandMark />
      </NavLink>

      <NavigationLinks />

      <div className="ms-desktop-rail__footer">
        <span
          className={`ms-desktop-rail__connection ms-desktop-rail__connection--${socketStatus}`}
          title={`Connection: ${socketStatus}`}
          aria-label={`Connection status: ${socketStatus}`}
        />
        <NavLink to="/settings" className="ms-desktop-rail__avatar" title={userLabel} aria-label="Open settings">
          <UserAvatar person={{ display_name: userLabel, avatar: userAvatar }} size="sm" shape="rounded" decorative />
        </NavLink>
        <button type="button" className="ms-desktop-rail__logout" onClick={onLogout} aria-label="Log out" title="Log out">
          <LogoutIcon />
        </button>
      </div>
    </aside>
  );
}

export function MobileBottomNavigation() {
  return (
    <aside className="ms-mobile-nav" aria-label="Crescentsphere navigation">
      <NavigationLinks mobile />
    </aside>
  );
}
