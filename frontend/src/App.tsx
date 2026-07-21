import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { ProtectedRoute } from "./app/ProtectedRoute";
import { RouteAccessibility } from "./components/RouteAccessibility";
import { AppShell } from "./components/AppShell";
import { useAuth } from "./contexts/AuthContext";
import { AuthCallbackPage } from "./pages/AuthCallbackPage";
import { AuthRedirectPage } from "./pages/AuthRedirectPage";
import { ConversationsPage } from "./pages/ConversationsPage";
import { ConversationPage } from "./pages/ConversationPage";
import { FriendsPage } from "./pages/FriendsPage";
import { GroupsPage } from "./pages/GroupsPage";
import { safeAppReturnPath } from "./lib/returnPath";

const CallsPage = lazy(() => import("./pages/CallsPage").then((module) => ({ default: module.CallsPage })));
const SettingsPage = lazy(() => import("./pages/SettingsPage").then((module) => ({ default: module.SettingsPage })));
const SupportChatPage = lazy(() => import("./pages/SupportChatPage").then((module) => ({ default: module.SupportChatPage })));
const SupportInvitationPage = lazy(() => import("./pages/SupportInvitationPage").then((module) => ({ default: module.SupportInvitationPage })));
const SupportPlansPage = lazy(() => import("./pages/SupportPlansPage").then((module) => ({ default: module.SupportPlansPage })));

function RouteLoadingFallback() {
  return <div className="route-loading" role="status" aria-live="polite">Loading…</div>;
}

export default function App() {
  const { isAuthenticated } = useAuth();
  const search = typeof window !== "undefined" ? new URLSearchParams(window.location.search) : null;
  const authReturnPath = safeAppReturnPath(search?.get("next"), "/chat");
  return (
    <>
      <RouteAccessibility />
      <Suspense fallback={<RouteLoadingFallback />}>
      <Routes>
        <Route path="/login" element={isAuthenticated ? <Navigate to={authReturnPath} replace /> : <AuthRedirectPage mode="login" />} />
        <Route path="/register" element={isAuthenticated ? <Navigate to={authReturnPath} replace /> : <AuthRedirectPage mode="signup" />} />
        <Route path="/forgot-password" element={<AuthRedirectPage mode="forgot-password" />} />
        <Route path="/auth/reset-password" element={<AuthRedirectPage mode="reset-password" />} />
        <Route path="/auth/verify-email" element={<AuthRedirectPage mode="verify-email" />} />
        <Route path="/auth/callback" element={<AuthCallbackPage />} />
        <Route path="/support/invitations/accept" element={<SupportInvitationPage />} />
        <Route path="/" element={<ProtectedRoute><AppShell /></ProtectedRoute>}>
          <Route index element={<Navigate to="/chat" replace />} />
          <Route path="chat" element={<ConversationsPage />} />
          <Route path="chat/:conversationId" element={<ConversationPage />} />
          <Route path="calls" element={<CallsPage />} />
          <Route path="calls/:callId" element={<></>} />
          <Route path="friends" element={<FriendsPage />} />
          <Route path="groups" element={<GroupsPage />} />
          <Route path="saved" element={<Navigate to="/chat" replace />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="support" element={<SupportChatPage />} />
          <Route path="support/plans" element={<SupportPlansPage />} />
          <Route path="support/inbox" element={<SupportChatPage />} />
          <Route path="support/websites" element={<SupportChatPage />} />
          <Route path="support/agents" element={<SupportChatPage />} />
          <Route path="support/analytics" element={<SupportChatPage />} />
          <Route path="support/knowledge" element={<SupportChatPage />} />
          <Route path="support/settings" element={<SupportChatPage />} />
        </Route>
        <Route path="*" element={<Navigate to={isAuthenticated ? "/chat" : "/login"} replace />} />
      </Routes>
      </Suspense>
    </>
  );
}
