import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { APP_NAME } from "../lib/config";

function routeLabel(pathname: string) {
  if (/^\/chat\/[^/]+\/?$/.test(pathname)) return "Conversation";
  if (pathname === "/chat" || pathname === "/") return "Chats";
  if (/^\/calls\/[^/]+\/?$/.test(pathname)) return "Call";
  if (pathname === "/calls") return "Calls";
  if (pathname === "/friends") return "Contacts";
  if (pathname === "/groups") return "Groups";
  if (pathname === "/settings") return "Settings";
  if (pathname === "/support/invitations/accept") return "Support Chat invitation";
  if (pathname === "/support/inbox") return "Support inbox";
  if (pathname === "/support/agents") return "Support agents";
  if (pathname === "/support/analytics") return "Support analytics";
  if (pathname === "/support/knowledge") return "Support knowledge";
  if (pathname === "/support/websites") return "Support websites";
  if (pathname === "/support/settings") return "Support settings";
  if (pathname === "/support") return "Support Chat";
  if (pathname === "/register") return "Create account";
  if (pathname === "/forgot-password") return "Reset password";
  if (pathname === "/auth/reset-password") return "Choose a new password";
  if (pathname === "/auth/verify-email") return "Verify email";
  if (pathname === "/auth/callback") return "Signing in";
  return "Sign in";
}

export function RouteAccessibility() {
  const location = useLocation();
  const [announcement, setAnnouncement] = useState("");

  useEffect(() => {
    const label = routeLabel(location.pathname);
    document.title = `${label} · ${APP_NAME}`;
    setAnnouncement("");

    const frame = window.requestAnimationFrame(() => {
      const main = document.getElementById("main-content");
      if (main) main.focus({ preventScroll: true });
      setAnnouncement(`${label} page`);
    });

    return () => window.cancelAnimationFrame(frame);
  }, [location.pathname]);

  return (
    <div className="ms-visually-hidden" role="status" aria-live="polite" aria-atomic="true">
      {announcement}
    </div>
  );
}
