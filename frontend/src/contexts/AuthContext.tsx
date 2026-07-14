import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { authApi } from "../api/auth";
import { chatApi } from "../api/chat";
import { ensureE2EEIdentity, getE2EEEnvironmentStatus } from "../lib/e2ee";
import { getOrCreateDeviceId } from "../lib/deviceIdentity";
import { chatSocket } from "../lib/chatSocket";
import { clearStoredWebPushToken, getStoredWebPushToken } from "../lib/pushNotifications";
import { AUTH_CLEARED_EVENT, clearTokens, getAccessToken, getRefreshToken, getSessionId, setAccessToken, setRefreshToken, setSessionId } from "../lib/tokenStore";
import { clearConversationDraftsForUser } from "../lib/conversationDrafts";
import { clearPrivateMediaCache } from "../lib/mediaPreviewCache";
import type { CurrentUser, LoginPayload, RegisterPayload } from "../types/auth";

type AuthContextValue = {
  user: CurrentUser | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (payload: LoginPayload) => Promise<void>;
  register: (payload: RegisterPayload) => Promise<{ emailVerificationRequired: boolean }>;
  confirmRegistrationCode: (email: string, code: string) => Promise<{ detail: string }>;
  resendRegistrationCode: (email: string) => Promise<{ detail: string }>;
  requestPasswordReset: (email: string) => Promise<{ detail: string }>;
  confirmPasswordReset: (token: string, newPassword: string) => Promise<{ detail: string; revoked_sessions?: number }>;
  confirmEmailVerification: (token: string) => Promise<{ detail: string }>;
  logout: () => Promise<void>;
  refreshMe: () => Promise<void>;
  setUser: (user: CurrentUser | null) => void;
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const refreshMe = async () => {
    const me = await authApi.me();
    setUser(me);
  };

  useEffect(() => {
    const bootstrap = async () => {
      if (!getAccessToken() && !getRefreshToken()) {
        setIsLoading(false);
        return;
      }
      try {
        await refreshMe();
      } catch {
        clearTokens();
      } finally {
        setIsLoading(false);
      }
    };
    void bootstrap();
  }, []);

  useEffect(() => {
    const handleAuthCleared = () => setUser(null);
    window.addEventListener(AUTH_CLEARED_EVENT, handleAuthCleared);
    return () => window.removeEventListener(AUTH_CLEARED_EVENT, handleAuthCleared);
  }, []);

  useEffect(() => {
    if (!user?.id) return;
    const userId = String(user.id);
    let cancelled = false;
    let retryTimer: number | null = null;
    let attempts = 0;

    const syncIdentity = async () => {
      if (cancelled || !getE2EEEnvironmentStatus().available) return;
      try {
        const identity = await ensureE2EEIdentity(userId);
        if (!cancelled && identity) {
          attempts = 0;
          queryClient.setQueryData(["e2ee-identity", userId], identity);
          if (identity.registrationChanged) {
            await queryClient.invalidateQueries({ queryKey: ["e2ee-devices"] });
            await queryClient.invalidateQueries({ queryKey: ["conversation-e2ee"] });
          }
        }
      } catch {
        attempts += 1;
        if (!cancelled && attempts <= 4) {
          retryTimer = window.setTimeout(() => void syncIdentity(), Math.min(30000, attempts * 5000));
        }
      }
    };

    const handleFocus = () => void syncIdentity();
    const handleVisibility = () => {
      if (document.visibilityState === "visible") void syncIdentity();
    };

    void syncIdentity();
    window.addEventListener("online", handleFocus);
    window.addEventListener("focus", handleFocus);
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      cancelled = true;
      if (retryTimer) window.clearTimeout(retryTimer);
      window.removeEventListener("online", handleFocus);
      window.removeEventListener("focus", handleFocus);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [queryClient, user?.id]);

  const login = async (payload: LoginPayload) => {
    const data = await authApi.login(payload);
    setAccessToken(data.access);
    setRefreshToken(data.refresh);
    if (data.session_id) setSessionId(data.session_id);
    await refreshMe();
  };

  const register = async (payload: RegisterPayload) => {
    const result = await authApi.register(payload);
    const emailVerificationRequired = Boolean(result?.email_verification_required);
    if (!emailVerificationRequired) {
      await login({ username: payload.username, password: payload.password });
    }
    return { emailVerificationRequired };
  };

  const confirmRegistrationCode = async (email: string, code: string) => authApi.confirmRegistrationCode(email, code);
  const resendRegistrationCode = async (email: string) => authApi.resendRegistrationCode(email);

  const requestPasswordReset = async (email: string) => authApi.requestPasswordReset(email);
  const confirmPasswordReset = async (token: string, newPassword: string) => authApi.confirmPasswordReset(token, newPassword);
  const confirmEmailVerification = async (token: string) => {
    const result = await authApi.confirmEmailVerification(token);
    if (getAccessToken() || getRefreshToken()) {
      await refreshMe().catch(() => undefined);
    }
    return result;
  };

  const logout = useCallback(async () => {
    const access = getAccessToken();
    const refresh = getRefreshToken();
    const sessionId = getSessionId();
    const deviceId = getOrCreateDeviceId();
    const pushToken = getStoredWebPushToken();

    const remoteCleanup = Promise.allSettled([
      authApi.logout({ refresh, sessionId, deviceId, accessToken: access }),
      chatApi.presenceDisconnect(deviceId, access),
      ...(pushToken ? [chatApi.deactivateDevice(pushToken, access)] : []),
    ]);

    // Local logout is immediate. Remote cleanup is best-effort and bounded so a
    // slow network can never trap the user on the settings screen.
    chatSocket.disconnect();
    queryClient.clear();
    if (user?.id) {
      const userId = String(user.id);
      clearConversationDraftsForUser(userId);
      void clearPrivateMediaCache(userId);
    }
    setUser(null);
    clearStoredWebPushToken();
    clearTokens();

    await Promise.race([
      remoteCleanup,
      new Promise<void>((resolve) => window.setTimeout(resolve, 1500)),
    ]);

  }, [queryClient, user?.id]);

  const value = useMemo(
    () => ({ user, isAuthenticated: !!user, isLoading, login, register, confirmRegistrationCode, resendRegistrationCode, requestPasswordReset, confirmPasswordReset, confirmEmailVerification, logout, refreshMe, setUser }),
    [user, isLoading, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}
