import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { chatApi } from "../api/chat";
import { authApi } from "../api/auth";
import { useAuth } from "../contexts/AuthContext";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { UserAvatar } from "../components/UserAvatar";
import { MessengerPageHeader } from "../components/pages/MessengerPageHeader";
import { ensureE2EEIdentity, formatFingerprint, getE2EEEnvironmentStatus, getStoredE2EEIdentity } from "../lib/e2ee";
import {
  clearStoredWebPushToken,
  ensureBrowserWebPushRegistration,
  getStoredWebPushToken,
  getWebPushPermissionMessage,
  getWebPushStatus,
} from "../lib/pushNotifications";
import { parseApiError, type ApiFieldErrors } from "../lib/apiErrors";
import {
  CALL_QUALITY_OPTIONS,
  describeNotificationDevice,
  describeSession,
  formatRelativeActivity,
} from "../lib/settingsPresentation";
import type { CurrentUser, UserProfile } from "../types/auth";
import type { NotificationPreferences } from "../types/chat";

function formatDateTime(value?: string | null) {
  if (!value) return "Not available";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Not available";
  return date.toLocaleString();
}

function downloadJson(filename: string, payload: unknown) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

function SectionStatus({ pending, message, error }: { pending?: boolean; message?: string | null; error?: string | null }) {
  if (error) return <div className="ms-page-error" role="alert">{error}</div>;
  if (pending) return <div className="ms-settings-section-status" role="status">Saving…</div>;
  if (message) return <div className="ms-page-success" role="status">{message}</div>;
  return null;
}

function SettingsToggle({
  title,
  description,
  checked,
  disabled,
  onChange,
}: {
  title: string;
  description: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className={`ms-settings-toggle ${disabled ? "is-disabled" : ""}`}>
      <span className="ms-settings-toggle__copy">
        <strong>{title}</strong>
        <small>{description}</small>
      </span>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
    </label>
  );
}

type SettingsConfirmation =
  | { kind: "session"; id: string; label: string }
  | { kind: "other-sessions"; count: number }
  | { kind: "secure-device"; id: string; label: string }
  | { kind: "notification-device"; token: string; label: string; current: boolean }
  | { kind: "unblock"; userId: string; label: string }
  | { kind: "delete-account" };

function confirmationContent(confirmation: SettingsConfirmation | null): {
  title: string;
  description: string;
  confirmLabel: string;
  tone: "default" | "danger";
} {
  if (!confirmation) return { title: "Confirm action", description: "", confirmLabel: "Continue", tone: "default" };
  switch (confirmation.kind) {
    case "session":
      return {
        title: "Log out this device?",
        description: `${confirmation.label} will need to sign in again. This does not affect the device you are using now.`,
        confirmLabel: "Log out device",
        tone: "danger",
      };
    case "other-sessions":
      return {
        title: "Log out other devices?",
        description: `${confirmation.count} other active ${confirmation.count === 1 ? "session" : "sessions"} will be ended. This device will stay signed in.`,
        confirmLabel: "Log out other devices",
        tone: "danger",
      };
    case "secure-device":
      return {
        title: "Remove secure device?",
        description: `${confirmation.label} will no longer be able to read new end-to-end encrypted messages until it registers again.`,
        confirmLabel: "Remove device",
        tone: "danger",
      };
    case "notification-device":
      return {
        title: confirmation.current ? "Turn off notifications here?" : "Disable notifications on this device?",
        description: confirmation.current
          ? "This browser will stop receiving message and call notifications."
          : `${confirmation.label} will stop receiving message and call notifications.`,
        confirmLabel: "Turn off notifications",
        tone: "danger",
      };
    case "unblock":
      return {
        title: `Unblock ${confirmation.label}?`,
        description: "They will be able to contact you again, subject to your other privacy settings.",
        confirmLabel: "Unblock",
        tone: "default",
      };
    case "delete-account":
      return {
        title: "Permanently delete this account?",
        description: "Your account will be deleted and personal profile data will be removed or anonymized. This cannot be undone.",
        confirmLabel: "Delete account permanently",
        tone: "danger",
      };
  }
}

function updateCurrentUserProfile(current: CurrentUser, patch: Partial<UserProfile>): CurrentUser {
  const normalizedPatch = { ...patch };
  if (normalizedPatch.is_discoverable === false) normalizedPatch.nearby_discovery_enabled = false;
  return {
    ...current,
    profile: {
      ...(current.profile ?? {}),
      ...normalizedPatch,
    },
  };
}

export function SettingsPage() {
  const queryClient = useQueryClient();
  const { user, setUser, refreshMe, logout } = useAuth();
  const preferencesQuery = useQuery({ queryKey: ["notification-preferences"], queryFn: chatApi.getNotificationPreferences });
  const meQuery = useQuery({ queryKey: ["me"], queryFn: authApi.me, initialData: user ?? undefined });
  const sessionsQuery = useQuery({ queryKey: ["sessions"], queryFn: ({ signal }) => authApi.listSessions(signal), enabled: !!user });
  const e2eeEnvironment = useMemo(() => getE2EEEnvironmentStatus(), []);
  const e2eeIdentityQuery = useQuery({
    queryKey: ["e2ee-identity", String(user?.id || "")],
    queryFn: () => ensureE2EEIdentity(String(user?.id || "")),
    enabled: Boolean(user?.id) && e2eeEnvironment.available,
    retry: 3,
    retryDelay: (attempt) => Math.min(15000, (attempt + 1) * 3000),
    staleTime: 5 * 60 * 1000,
  });
  const e2eeDevicesQuery = useQuery({ queryKey: ["e2ee-devices"], queryFn: ({ signal }) => chatApi.listE2EEDeviceKeys(signal), enabled: !!user });
  const chatDevicesQuery = useQuery({ queryKey: ["chat-devices"], queryFn: ({ signal }) => chatApi.listDevices(signal), enabled: !!user });
  const blocksQuery = useQuery({ queryKey: ["chat-blocks"], queryFn: ({ signal }) => chatApi.listBlocks(signal), enabled: !!user });

  const [form, setForm] = useState({
    first_name: "",
    last_name: "",
    email: "",
    display_name: "",
    bio: "",
    status_message: "",
  });
  const [passwordForm, setPasswordForm] = useState({ current: "", next: "", confirm: "" });
  const [deletePassword, setDeletePassword] = useState("");
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const [profileMessage, setProfileMessage] = useState<string | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [profileFieldErrors, setProfileFieldErrors] = useState<ApiFieldErrors>({});
  const [avatarMessage, setAvatarMessage] = useState<string | null>(null);
  const [avatarError, setAvatarError] = useState<string | null>(null);
  const [passwordFieldErrors, setPasswordFieldErrors] = useState<ApiFieldErrors>({});
  const [emailVerificationMessage, setEmailVerificationMessage] = useState<string | null>(null);
  const [emailVerificationError, setEmailVerificationError] = useState<string | null>(null);
  const [securityMessage, setSecurityMessage] = useState<string | null>(null);
  const [securityError, setSecurityError] = useState<string | null>(null);
  const [sessionMessage, setSessionMessage] = useState<string | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [privacyMessage, setPrivacyMessage] = useState<string | null>(null);
  const [privacyError, setPrivacyError] = useState<string | null>(null);
  const [preferenceArea, setPreferenceArea] = useState<"notifications" | "calls" | null>(null);
  const [preferenceMessage, setPreferenceMessage] = useState<string | null>(null);
  const [preferenceError, setPreferenceError] = useState<string | null>(null);
  const [pushMessage, setPushMessage] = useState<string | null>(null);
  const [pushError, setPushError] = useState<string | null>(null);
  const [deviceMessage, setDeviceMessage] = useState<string | null>(null);
  const [deviceError, setDeviceError] = useState<string | null>(null);
  const [dataMessage, setDataMessage] = useState<string | null>(null);
  const [dataError, setDataError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<SettingsConfirmation | null>(null);
  const [confirmationError, setConfirmationError] = useState<string | null>(null);
  const [webPushState, setWebPushState] = useState({ supported: false, configured: false, permission: "default", token: "" });

  const currentProfile = meQuery.data ?? user;
  const profileHasChanges = Boolean(currentProfile) && (
    form.first_name !== (currentProfile?.first_name ?? "")
    || form.last_name !== (currentProfile?.last_name ?? "")
    || form.email !== (currentProfile?.email ?? "")
    || form.display_name !== (currentProfile?.profile?.display_name ?? "")
    || form.bio !== (currentProfile?.profile?.bio ?? "")
    || form.status_message !== (currentProfile?.profile?.status_message ?? "")
  );

  useEffect(() => {
    const source = meQuery.data ?? user;
    if (!source) return;
    setForm({
      first_name: source.first_name ?? "",
      last_name: source.last_name ?? "",
      email: source.email ?? "",
      display_name: source.profile?.display_name ?? "",
      bio: source.profile?.bio ?? "",
      status_message: source.profile?.status_message ?? "",
    });
  }, [meQuery.data, user]);

  useEffect(() => {
    void getWebPushStatus().then(setWebPushState).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (e2eeIdentityQuery.data) {
      void queryClient.invalidateQueries({ queryKey: ["e2ee-devices"] });
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
    }
  }, [e2eeIdentityQuery.data, queryClient]);

  useEffect(() => {
    if (!profileHasChanges) return;
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [profileHasChanges]);

  const profileMutation = useMutation({
    mutationFn: () => authApi.updateMe({
      first_name: form.first_name.trim(),
      last_name: form.last_name.trim(),
      email: form.email.trim(),
      profile: {
        display_name: form.display_name.trim(),
        bio: form.bio,
        status_message: form.status_message.trim(),
      },
    }),
    onMutate: () => {
      setProfileMessage(null);
      setProfileError(null);
      setProfileFieldErrors({});
    },
    onSuccess: async (nextUser) => {
      const emailChanged = (user?.email ?? "").toLowerCase() !== (nextUser.email ?? "").toLowerCase();
      setUser(nextUser);
      queryClient.setQueryData(["me"], nextUser);
      setForm({
        first_name: nextUser.first_name ?? "",
        last_name: nextUser.last_name ?? "",
        email: nextUser.email ?? "",
        display_name: nextUser.profile?.display_name ?? "",
        bio: nextUser.profile?.bio ?? "",
        status_message: nextUser.profile?.status_message ?? "",
      });
      setProfileMessage(emailChanged ? "Profile saved. Check your new email address to verify it." : "Profile saved.");
      await queryClient.invalidateQueries({ queryKey: ["me"] });
      await refreshMe().catch(() => undefined);
    },
    onError: (error) => {
      const parsed = parseApiError(error, "Unable to save your profile right now.");
      setProfileError(parsed.message);
      setProfileFieldErrors(parsed.fields);
    },
  });

  const refreshIdentityCaches = async (nextUser: CurrentUser) => {
    setUser(nextUser);
    queryClient.setQueryData(["me"], nextUser);
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["conversations"] }),
      queryClient.invalidateQueries({ queryKey: ["friend-requests"] }),
      queryClient.invalidateQueries({ queryKey: ["user-search"] }),
      queryClient.invalidateQueries({ queryKey: ["recent-calls"] }),
    ]);
  };

  const avatarMutation = useMutation({
    mutationFn: (file: File) => authApi.uploadAvatar(file),
    onMutate: () => {
      setAvatarMessage(null);
      setAvatarError(null);
    },
    onSuccess: async (nextUser) => {
      await refreshIdentityCaches(nextUser);
      setAvatarMessage("Profile picture updated.");
    },
    onError: (error) => setAvatarError(parseApiError(error, "Unable to update your profile picture.").message),
  });

  const removeAvatarMutation = useMutation({
    mutationFn: authApi.deleteAvatar,
    onMutate: () => {
      setAvatarMessage(null);
      setAvatarError(null);
    },
    onSuccess: async (nextUser) => {
      await refreshIdentityCaches(nextUser);
      setAvatarMessage("Profile picture removed.");
    },
    onError: (error) => setAvatarError(parseApiError(error, "Unable to remove your profile picture.").message),
  });

  const handleAvatarFile = (file?: File) => {
    if (!file) return;
    setAvatarMessage(null);
    setAvatarError(null);
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      setAvatarError("Choose a JPEG, PNG, or WebP image.");
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      setAvatarError("Profile pictures must be 5 MB or smaller.");
      return;
    }
    avatarMutation.mutate(file);
  };

  const passwordMutation = useMutation({
    mutationFn: () => authApi.changePassword(passwordForm.current, passwordForm.next),
    onMutate: () => {
      setSecurityError(null);
      setSecurityMessage(null);
      setPasswordFieldErrors({});
    },
    onSuccess: async (payload) => {
      setPasswordForm({ current: "", next: "", confirm: "" });
      setSecurityMessage(payload.detail || "Password updated.");
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
    onError: (error) => {
      const parsed = parseApiError(error, "Unable to update the password.");
      setSecurityError(parsed.message);
      setPasswordFieldErrors(parsed.fields);
    },
  });

  const emailVerifyMutation = useMutation({
    mutationFn: authApi.requestEmailVerification,
    onMutate: () => {
      setEmailVerificationError(null);
      setEmailVerificationMessage(null);
    },
    onSuccess: (payload) => setEmailVerificationMessage(payload.detail),
    onError: (error) => setEmailVerificationError(parseApiError(error, "Unable to send the verification email.").message),
  });

  const preferenceMutation = useMutation({
    mutationFn: ({ patch }: { area: "notifications" | "calls"; patch: Partial<NotificationPreferences> }) => chatApi.updateNotificationPreferences(patch),
    onMutate: async ({ area, patch }) => {
      setPreferenceArea(area);
      setPreferenceMessage(null);
      setPreferenceError(null);
      await queryClient.cancelQueries({ queryKey: ["notification-preferences"] });
      const previous = queryClient.getQueryData<NotificationPreferences>(["notification-preferences"]);
      queryClient.setQueryData<NotificationPreferences>(["notification-preferences"], (current) => ({
        call_quality_preference: current?.call_quality_preference ?? "auto",
        ...(current ?? {}),
        ...patch,
      }));
      return { previous };
    },
    onSuccess: (nextPreferences, variables) => {
      queryClient.setQueryData(["notification-preferences"], nextPreferences);
      setPreferenceArea(variables.area);
      setPreferenceMessage("Saved automatically.");
    },
    onError: (error, variables, context) => {
      if (context?.previous) queryClient.setQueryData(["notification-preferences"], context.previous);
      setPreferenceArea(variables.area);
      setPreferenceError(parseApiError(error, "Unable to save this setting.").message);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["notification-preferences"] }),
  });

  const privacyMutation = useMutation({
    mutationFn: (patch: Partial<UserProfile>) => authApi.updateMe({ profile: patch }),
    onMutate: async (patch) => {
      setPrivacyMessage(null);
      setPrivacyError(null);
      await queryClient.cancelQueries({ queryKey: ["me"] });
      const previous = queryClient.getQueryData<CurrentUser>(["me"]) ?? user ?? null;
      if (previous) {
        const optimistic = updateCurrentUserProfile(previous, patch);
        queryClient.setQueryData(["me"], optimistic);
        setUser(optimistic);
      }
      return { previous };
    },
    onSuccess: (nextUser) => {
      queryClient.setQueryData(["me"], nextUser);
      setUser(nextUser);
      setPrivacyMessage("Privacy settings saved.");
    },
    onError: (error, _patch, context) => {
      if (context?.previous) {
        queryClient.setQueryData(["me"], context.previous);
        setUser(context.previous);
      }
      setPrivacyError(parseApiError(error, "Unable to save your privacy settings.").message);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["me"] }),
  });

  const revokeSessionMutation = useMutation({
    mutationFn: (sessionId: string) => authApi.revokeSession(sessionId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sessions"] }),
  });
  const revokeOtherSessionsMutation = useMutation({
    mutationFn: authApi.revokeOtherSessions,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sessions"] }),
  });
  const revokeDeviceMutation = useMutation({
    mutationFn: (keyId: string) => chatApi.revokeE2EEDeviceKey(keyId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["e2ee-devices"] });
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
  const deactivatePushDeviceMutation = useMutation({
    mutationFn: (pushToken: string) => chatApi.deactivateDevice(pushToken),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["chat-devices"] }),
  });
  const unblockMutation = useMutation({
    mutationFn: (userId: string) => chatApi.unblockUser(userId),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["chat-blocks"] }),
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
        queryClient.invalidateQueries({ queryKey: ["friend-requests"] }),
        queryClient.invalidateQueries({ queryKey: ["user-search"] }),
        queryClient.invalidateQueries({ queryKey: ["nearby-users"] }),
      ]);
    },
  });

  const registerWebPushMutation = useMutation({
    mutationFn: async () => {
      const token = await ensureBrowserWebPushRegistration({ interactive: true });
      if (!token) throw new Error("This browser could not enable notifications.");
      await chatApi.registerDevice({ platform: "web", push_token: token });
      return token;
    },
    onMutate: () => {
      setPushError(null);
      setPushMessage(null);
    },
    onSuccess: async () => {
      setWebPushState(await getWebPushStatus());
      setPushMessage("Notifications are enabled on this browser.");
      await queryClient.invalidateQueries({ queryKey: ["chat-devices"] });
    },
    onError: (error) => setPushError(error instanceof Error ? error.message : "Unable to enable notifications on this browser."),
  });

  const exportMutation = useMutation({
    mutationFn: authApi.exportAccount,
    onMutate: () => {
      setDataError(null);
      setDataMessage(null);
    },
    onSuccess: (payload) => {
      downloadJson(`account-export-${new Date().toISOString().slice(0, 10)}.json`, payload);
      setDataMessage("Your account export was downloaded.");
    },
    onError: (error) => setDataError(parseApiError(error, "Unable to prepare your account export.").message),
  });

  const logoutMutation = useMutation({
    mutationFn: logout,
    onError: (error) => setSecurityError(error instanceof Error ? error.message : "Unable to finish logout."),
  });

  const deleteMutation = useMutation({
    mutationFn: () => authApi.deleteAccount(deletePassword),
    onMutate: () => setDeleteError(null),
    onSuccess: async () => {
      setDeletePassword("");
      setDeleteConfirmation("");
      await logout();
      window.location.assign("/login");
    },
    onError: (error) => {
      const parsed = parseApiError(error, "Unable to delete this account.");
      setDeleteError(parsed.fields.password || parsed.message);
    },
  });

  const activeSessions = useMemo(() => (sessionsQuery.data ?? []).filter((session) => !session.revoked_at), [sessionsQuery.data]);
  const otherSessions = useMemo(() => activeSessions.filter((session) => !session.is_current), [activeSessions]);
  const currentE2EEIdentity = useMemo(() => user?.id ? getStoredE2EEIdentity(String(user.id)) : null, [user?.id, e2eeIdentityQuery.data]);
  const secureDevices = useMemo(() => (e2eeDevicesQuery.data ?? []).filter((device) => device.is_active && !device.revoked_at), [e2eeDevicesQuery.data]);
  const activePushDevices = useMemo(() => (chatDevicesQuery.data ?? []).filter((device) => device.is_active), [chatDevicesQuery.data]);
  const currentWebPushToken = getStoredWebPushToken();
  const otherPushDevices = useMemo(
    () => activePushDevices.filter((device) => !currentWebPushToken || device.push_token !== currentWebPushToken),
    [activePushDevices, currentWebPushToken],
  );
  const activePreset = preferencesQuery.data?.call_quality_preference ?? "auto";
  const deleteConfirmationMatches = Boolean(user?.username) && deleteConfirmation.trim() === user?.username;
  const confirmationView = confirmationContent(confirmation);
  const confirmationPending = revokeSessionMutation.isPending
    || revokeOtherSessionsMutation.isPending
    || revokeDeviceMutation.isPending
    || deactivatePushDeviceMutation.isPending
    || unblockMutation.isPending
    || deleteMutation.isPending;

  const resetProfileForm = () => {
    if (!currentProfile) return;
    setForm({
      first_name: currentProfile.first_name ?? "",
      last_name: currentProfile.last_name ?? "",
      email: currentProfile.email ?? "",
      display_name: currentProfile.profile?.display_name ?? "",
      bio: currentProfile.profile?.bio ?? "",
      status_message: currentProfile.profile?.status_message ?? "",
    });
    setProfileError(null);
    setProfileMessage(null);
    setProfileFieldErrors({});
  };

  const handleConfirmation = async () => {
    if (!confirmation) return;
    setConfirmationError(null);
    try {
      switch (confirmation.kind) {
        case "session":
          await revokeSessionMutation.mutateAsync(confirmation.id);
          setSessionMessage(`${confirmation.label} was logged out.`);
          setSessionError(null);
          break;
        case "other-sessions":
          await revokeOtherSessionsMutation.mutateAsync();
          setSessionMessage("Other devices were logged out.");
          setSessionError(null);
          break;
        case "secure-device":
          await revokeDeviceMutation.mutateAsync(confirmation.id);
          setDeviceMessage(`${confirmation.label} was removed from secure messaging.`);
          setDeviceError(null);
          break;
        case "notification-device":
          await deactivatePushDeviceMutation.mutateAsync(confirmation.token);
          if (confirmation.current) {
            clearStoredWebPushToken();
            setWebPushState(await getWebPushStatus());
            setPushMessage("Notifications are disabled on this browser.");
            setPushError(null);
          } else {
            setPushMessage(`${confirmation.label} will no longer receive notifications.`);
            setPushError(null);
          }
          break;
        case "unblock":
          await unblockMutation.mutateAsync(confirmation.userId);
          setPrivacyMessage(`${confirmation.label} was unblocked.`);
          setPrivacyError(null);
          break;
        case "delete-account":
          await deleteMutation.mutateAsync();
          return;
      }
      setConfirmation(null);
    } catch (error) {
      const message = parseApiError(error, "This action could not be completed.").message;
      setConfirmationError(message);
      if (confirmation.kind === "session" || confirmation.kind === "other-sessions") setSessionError(message);
      if (confirmation.kind === "secure-device") setDeviceError(message);
      if (confirmation.kind === "notification-device") setPushError(message);
      if (confirmation.kind === "unblock") setPrivacyError(message);
    }
  };

  const profile = currentProfile?.profile;
  const isDiscoverable = profile?.is_discoverable !== false;
  const showOnlineStatus = profile?.show_online_status !== false;
  const nearbyDiscoveryEnabled = Boolean(profile?.nearby_discovery_enabled);

  return (
    <div className="ms-workspace-page ms-settings-page">
      <MessengerPageHeader
        eyebrow="Settings"
        title="Settings"
        description="Manage your profile, privacy, notifications, calls, and connected devices."
      />

      <div className="ms-settings-page__body">
        <nav className="ms-settings-page__nav" aria-label="Settings sections">
          <a href="#profile">Profile</a>
          <a href="#account">Account</a>
          <a href="#sessions">Sessions</a>
          <a href="#privacy">Privacy</a>
          <a href="#notifications">Notifications</a>
          <a href="#calling">Calls</a>
          <a href="#devices">Secure devices</a>
          <a href="#data">Your data</a>
        </nav>

        <div className="ms-settings-page__grid">
          <section className="ms-settings-page__main" aria-label="Account settings">
            <section id="profile" className="ms-page-surface ms-page-surface--padded ms-settings-card">
              <div className="ms-section-header">
                <div className="ms-section-header__copy">
                  <div className="ms-section-header__eyebrow">Profile</div>
                  <h2>Your profile</h2>
                  <p>Choose how your name and profile information appear to other people.</p>
                </div>
                <span className={`ms-settings-save-state ${profileHasChanges ? "is-unsaved" : ""}`} aria-live="polite">
                  {profileMutation.isPending ? "Saving…" : profileHasChanges ? "Unsaved changes" : "Saved"}
                </span>
              </div>
              <div className="ms-settings-avatar-editor">
                <UserAvatar
                  person={{
                    display_name: currentProfile?.profile?.display_name || currentProfile?.display_name || currentProfile?.username || "Profile",
                    username: currentProfile?.username,
                    avatar: currentProfile?.profile?.avatar,
                  }}
                  size="xl"
                  className="ms-settings-avatar-editor__preview"
                />
                <div className="ms-settings-avatar-editor__copy">
                  <strong>Profile picture</strong>
                  <p>Shown in chats, friend lists, calls, and your account menu.</p>
                  <div className="ms-page-actions ms-page-actions--wrap">
                    <label className={`ms-button ms-button--compact ${avatarMutation.isPending || removeAvatarMutation.isPending ? "is-disabled" : ""}`}>
                      {avatarMutation.isPending ? "Uploading…" : currentProfile?.profile?.avatar ? "Replace picture" : "Choose picture"}
                      <input
                        className="ms-settings-avatar-editor__input"
                        type="file"
                        accept="image/jpeg,image/png,image/webp"
                        disabled={avatarMutation.isPending || removeAvatarMutation.isPending}
                        onChange={(event) => {
                          const file = event.target.files?.[0];
                          event.target.value = "";
                          handleAvatarFile(file);
                        }}
                      />
                    </label>
                    {currentProfile?.profile?.avatar ? (
                      <button
                        type="button"
                        className="ms-button ms-button--compact ms-button--danger-text"
                        disabled={avatarMutation.isPending || removeAvatarMutation.isPending}
                        onClick={() => removeAvatarMutation.mutate()}
                      >
                        {removeAvatarMutation.isPending ? "Removing…" : "Remove"}
                      </button>
                    ) : null}
                  </div>
                  <small>JPEG, PNG, or WebP. Maximum 5 MB.</small>
                </div>
              </div>
              <SectionStatus pending={avatarMutation.isPending || removeAvatarMutation.isPending} message={avatarMessage} error={avatarError} />

              <form className="ms-settings-form" onSubmit={(event) => { event.preventDefault(); profileMutation.mutate(); }} noValidate>
                <label className="ms-settings-field">
                  <span>First name</span>
                  <input value={form.first_name} onChange={(event) => setForm((current) => ({ ...current, first_name: event.target.value }))} aria-invalid={Boolean(profileFieldErrors.first_name)} aria-describedby={profileFieldErrors.first_name ? "profile-first-name-error" : undefined} maxLength={150} autoComplete="given-name" />
                  {profileFieldErrors.first_name ? <small id="profile-first-name-error" className="ms-settings-field__error" role="alert">{profileFieldErrors.first_name}</small> : null}
                </label>
                <label className="ms-settings-field">
                  <span>Last name</span>
                  <input value={form.last_name} onChange={(event) => setForm((current) => ({ ...current, last_name: event.target.value }))} aria-invalid={Boolean(profileFieldErrors.last_name)} aria-describedby={profileFieldErrors.last_name ? "profile-last-name-error" : undefined} maxLength={150} autoComplete="family-name" />
                  {profileFieldErrors.last_name ? <small id="profile-last-name-error" className="ms-settings-field__error" role="alert">{profileFieldErrors.last_name}</small> : null}
                </label>
                <label className="ms-settings-field ms-settings-field--wide">
                  <span>Email address</span>
                  <input type="email" autoComplete="email" value={form.email} onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))} aria-invalid={Boolean(profileFieldErrors.email)} aria-describedby={profileFieldErrors.email ? "profile-email-error" : "profile-email-help"} maxLength={254} required />
                  {profileFieldErrors.email ? <small id="profile-email-error" className="ms-settings-field__error" role="alert">{profileFieldErrors.email}</small> : <small id="profile-email-help">Changing your email address requires verification again.</small>}
                </label>
                <label className="ms-settings-field ms-settings-field--wide">
                  <span>Display name</span>
                  <input value={form.display_name} onChange={(event) => setForm((current) => ({ ...current, display_name: event.target.value }))} aria-invalid={Boolean(profileFieldErrors["profile.display_name"])} aria-describedby={profileFieldErrors["profile.display_name"] ? "profile-display-name-error" : "profile-display-name-help"} maxLength={150} autoComplete="nickname" />
                  {profileFieldErrors["profile.display_name"] ? <small id="profile-display-name-error" className="ms-settings-field__error" role="alert">{profileFieldErrors["profile.display_name"]}</small> : <small id="profile-display-name-help">Shown in conversations when available.</small>}
                </label>
                <label className="ms-settings-field ms-settings-field--wide">
                  <span>Bio</span>
                  <textarea value={form.bio} onChange={(event) => setForm((current) => ({ ...current, bio: event.target.value }))} aria-invalid={Boolean(profileFieldErrors["profile.bio"])} aria-describedby={profileFieldErrors["profile.bio"] ? "profile-bio-error" : undefined} rows={4} maxLength={1000} />
                  {profileFieldErrors["profile.bio"] ? <small id="profile-bio-error" className="ms-settings-field__error" role="alert">{profileFieldErrors["profile.bio"]}</small> : null}
                </label>
                <label className="ms-settings-field ms-settings-field--wide">
                  <span>Status message</span>
                  <input value={form.status_message} onChange={(event) => setForm((current) => ({ ...current, status_message: event.target.value }))} aria-invalid={Boolean(profileFieldErrors["profile.status_message"])} aria-describedby={profileFieldErrors["profile.status_message"] ? "profile-status-error" : undefined} maxLength={255} />
                  {profileFieldErrors["profile.status_message"] ? <small id="profile-status-error" className="ms-settings-field__error" role="alert">{profileFieldErrors["profile.status_message"]}</small> : null}
                </label>
                <div className="ms-page-actions ms-page-actions--wrap ms-settings-field--wide">
                  <button type="submit" className="ms-button ms-button--primary" disabled={profileMutation.isPending || !profileHasChanges || !form.email.trim()}>
                    {profileMutation.isPending ? "Saving…" : "Save changes"}
                  </button>
                  {profileHasChanges ? <button type="button" className="ms-button" disabled={profileMutation.isPending} onClick={resetProfileForm}>Discard changes</button> : null}
                </div>
              </form>
              <SectionStatus message={profileMessage} error={profileError} />
            </section>

            <section id="account" className="ms-page-surface ms-page-surface--padded ms-settings-card">
              <div className="ms-section-header">
                <div className="ms-section-header__copy">
                  <div className="ms-section-header__eyebrow">Account</div>
                  <h2>Account and password</h2>
                  <p>Verify your email, update your password, or sign out of this browser.</p>
                </div>
              </div>

              <div className="ms-settings-list">
                <div className="ms-settings-row">
                  <div className="ms-settings-row__copy">
                    <strong>Email verification</strong>
                    <div className="muted">{currentProfile?.email || "No email address"}</div>
                    <div className="muted">{currentProfile?.email_verified ? `Verified ${formatRelativeActivity(currentProfile.email_verified_at)}` : "Verification is still required."}</div>
                  </div>
                  {!currentProfile?.email_verified ? (
                    <div className="ms-page-actions">
                      <button type="button" className="ms-button ms-button--compact" disabled={emailVerifyMutation.isPending || !currentProfile?.email} onClick={() => emailVerifyMutation.mutate()}>
                        {emailVerifyMutation.isPending ? "Sending…" : "Send verification email"}
                      </button>
                    </div>
                  ) : <span className="ms-settings-badge">Verified</span>}
                </div>
                {(currentProfile?.social_accounts ?? []).length ? (
                  <div className="ms-settings-row">
                    <div className="ms-settings-row__copy">
                      <strong>Connected sign-in accounts</strong>
                      <div className="muted">
                        {currentProfile?.social_accounts?.map((item) => `${item.provider}${item.email ? ` · ${item.email}` : ""}`).join(" · ")}
                      </div>
                    </div>
                  </div>
                ) : null}
              </div>
              {emailVerificationError ? <div className="ms-page-error" role="alert">{emailVerificationError}</div> : null}
              {emailVerificationMessage ? <div className="ms-page-success" role="status">{emailVerificationMessage}</div> : null}

              <div className="ms-settings-subsection">
                <div className="ms-settings-subsection__header">
                  <div>
                    <h3>Change password</h3>
                    <p>Use a strong password that you do not use on another service.</p>
                  </div>
                </div>
                <form className="ms-settings-form" onSubmit={(event) => { event.preventDefault(); passwordMutation.mutate(); }} noValidate>
                  <label className="ms-settings-field ms-settings-field--wide">
                    <span>Current password</span>
                    <input value={passwordForm.current} onChange={(event) => setPasswordForm((current) => ({ ...current, current: event.target.value }))} type="password" autoComplete="current-password" aria-invalid={Boolean(passwordFieldErrors.current_password)} aria-describedby={passwordFieldErrors.current_password ? "current-password-error" : undefined} />
                    {passwordFieldErrors.current_password ? <small id="current-password-error" className="ms-settings-field__error" role="alert">{passwordFieldErrors.current_password}</small> : null}
                  </label>
                  <label className="ms-settings-field">
                    <span>New password</span>
                    <input value={passwordForm.next} onChange={(event) => setPasswordForm((current) => ({ ...current, next: event.target.value }))} type="password" autoComplete="new-password" aria-invalid={Boolean(passwordFieldErrors.new_password)} aria-describedby={passwordFieldErrors.new_password ? "new-password-error" : "new-password-help"} minLength={8} />
                    {passwordFieldErrors.new_password ? <small id="new-password-error" className="ms-settings-field__error" role="alert">{passwordFieldErrors.new_password}</small> : <small id="new-password-help">Use at least 8 characters.</small>}
                  </label>
                  <label className="ms-settings-field">
                    <span>Confirm new password</span>
                    <input value={passwordForm.confirm} onChange={(event) => setPasswordForm((current) => ({ ...current, confirm: event.target.value }))} type="password" autoComplete="new-password" aria-invalid={Boolean(passwordForm.confirm && passwordForm.next !== passwordForm.confirm)} aria-describedby={passwordForm.confirm && passwordForm.next !== passwordForm.confirm ? "confirm-password-error" : undefined} />
                    {passwordForm.confirm && passwordForm.next !== passwordForm.confirm ? <small id="confirm-password-error" className="ms-settings-field__error" role="alert">Passwords do not match.</small> : null}
                  </label>
                  <div className="ms-page-actions ms-settings-field--wide">
                    <button type="submit" className="ms-button ms-button--primary" disabled={passwordMutation.isPending || !passwordForm.current || !passwordForm.next || passwordForm.next !== passwordForm.confirm}>
                      {passwordMutation.isPending ? "Updating…" : "Change password"}
                    </button>
                  </div>
                </form>
                <SectionStatus message={securityMessage} error={securityError} />
              </div>

              <div className="ms-settings-subsection ms-settings-subsection--compact">
                <div className="ms-settings-row">
                  <div className="ms-settings-row__copy">
                    <strong>Log out of this browser</strong>
                    <div className="muted">Your local account data and this browser’s Crescentsphere session will be cleared.</div>
                  </div>
                  <div className="ms-page-actions">
                    <button type="button" className="ms-button ms-button--compact" disabled={logoutMutation.isPending} onClick={() => logoutMutation.mutate()}>
                      {logoutMutation.isPending ? "Logging out…" : "Log out"}
                    </button>
                  </div>
                </div>
              </div>
            </section>

            <section id="sessions" className="ms-page-surface ms-page-surface--padded ms-settings-card">
              <div className="ms-section-header">
                <div className="ms-section-header__copy">
                  <div className="ms-section-header__eyebrow">Security</div>
                  <h2>Active sessions</h2>
                  <p>Review browsers and devices where your account is currently signed in.</p>
                </div>
                {otherSessions.length ? (
                  <button type="button" className="ms-button ms-button--compact" onClick={() => { setConfirmationError(null); setConfirmation({ kind: "other-sessions", count: otherSessions.length }); }}>
                    Log out other devices
                  </button>
                ) : null}
              </div>
              {sessionsQuery.isLoading ? <div className="ms-settings-empty">Loading active sessions…</div> : null}
              {sessionsQuery.isError ? (
                <div className="ms-page-error">
                  Unable to load active sessions.
                  <button type="button" className="ms-button ms-button--compact" onClick={() => void sessionsQuery.refetch()}>Retry</button>
                </div>
              ) : null}
              <div className="ms-settings-list">
                {activeSessions.map((session) => {
                  const label = describeSession(session);
                  return (
                    <div key={session.id} className="ms-settings-row">
                      <div className="ms-settings-row__copy">
                        <div className="ms-settings-row__title">
                          <strong>{label}</strong>
                          {session.is_current ? <span className="ms-settings-badge">This device</span> : null}
                        </div>
                        <div className="muted">{session.is_current ? "Active now" : `Last active ${formatRelativeActivity(session.last_seen_at)}`}</div>
                        <div className="muted">{session.ip_address ? `IP ${session.ip_address} · ` : ""}Session expires {formatDateTime(session.expires_at)}</div>
                      </div>
                      {!session.is_current ? (
                        <div className="ms-page-actions">
                          <button type="button" className="ms-button ms-button--compact ms-button--danger-text" onClick={() => { setConfirmationError(null); setConfirmation({ kind: "session", id: session.id, label }); }}>
                            Log out
                          </button>
                        </div>
                      ) : null}
                    </div>
                  );
                })}
                {!sessionsQuery.isLoading && !sessionsQuery.isError && !activeSessions.length ? <div className="ms-settings-empty">No active sessions were found.</div> : null}
              </div>
              <SectionStatus message={sessionMessage} error={sessionError} />
            </section>

            <section id="privacy" className="ms-page-surface ms-page-surface--padded ms-settings-card">
              <div className="ms-section-header">
                <div className="ms-section-header__copy">
                  <div className="ms-section-header__eyebrow">Privacy</div>
                  <h2>Privacy and blocked users</h2>
                  <p>Control whether people can find you and what presence information they can see.</p>
                </div>
              </div>
              <div className="ms-settings-list">
                <SettingsToggle
                  title="Show when I am online"
                  description="People in your conversations can see your online and last-active status."
                  checked={showOnlineStatus}
                  disabled={privacyMutation.isPending}
                  onChange={(checked) => privacyMutation.mutate({ show_online_status: checked })}
                />
                <SettingsToggle
                  title="Allow people to find my account"
                  description="Your profile can appear in username and people search."
                  checked={isDiscoverable}
                  disabled={privacyMutation.isPending}
                  onChange={(checked) => privacyMutation.mutate({ is_discoverable: checked, ...(checked ? {} : { nearby_discovery_enabled: false }) })}
                />
                <SettingsToggle
                  title="Appear in nearby discovery"
                  description={isDiscoverable ? "Nearby discovery may use your recently shared location." : "Turn on account discovery before enabling nearby discovery."}
                  checked={nearbyDiscoveryEnabled}
                  disabled={privacyMutation.isPending || !isDiscoverable}
                  onChange={(checked) => privacyMutation.mutate({ nearby_discovery_enabled: checked })}
                />
              </div>
              <SectionStatus pending={privacyMutation.isPending} message={privacyMessage} error={privacyError} />

              <div className="ms-settings-subsection">
                <div className="ms-settings-subsection__header">
                  <div>
                    <h3>Blocked users</h3>
                    <p>Blocked people cannot contact you directly.</p>
                  </div>
                </div>
                {blocksQuery.isLoading ? <div className="ms-settings-empty">Loading blocked users…</div> : null}
                {blocksQuery.isError ? (
                  <div className="ms-page-error">
                    Unable to load blocked users.
                    <button type="button" className="ms-button ms-button--compact" onClick={() => void blocksQuery.refetch()}>Retry</button>
                  </div>
                ) : null}
                <div className="ms-settings-list">
                  {(blocksQuery.data ?? []).map((block) => {
                    const label = block.blocked.display_name || block.blocked.username;
                    return (
                      <div key={block.id} className="ms-settings-row">
                        <div className="ms-settings-row__copy">
                          <strong>{label}</strong>
                          <div className="muted">Blocked {formatRelativeActivity(block.created_at)}</div>
                        </div>
                        <div className="ms-page-actions">
                          <button type="button" className="ms-button ms-button--compact" onClick={() => { setConfirmationError(null); setConfirmation({ kind: "unblock", userId: block.blocked.id, label }); }}>
                            Unblock
                          </button>
                        </div>
                      </div>
                    );
                  })}
                  {!blocksQuery.isLoading && !blocksQuery.isError && !(blocksQuery.data ?? []).length ? <div className="ms-settings-empty">You have not blocked anyone.</div> : null}
                </div>
              </div>
            </section>
          </section>

          <aside className="ms-settings-page__side" aria-label="Messaging settings">
            <section id="notifications" className="ms-page-surface ms-page-surface--padded ms-settings-card">
              <div className="ms-section-header">
                <div className="ms-section-header__copy">
                  <div className="ms-section-header__eyebrow">Notifications</div>
                  <h2>Messages and calls</h2>
                  <p>Choose what appears in notifications and where they are delivered.</p>
                </div>
              </div>
              {preferencesQuery.isLoading ? <div className="ms-settings-empty">Loading notification settings…</div> : null}
              {preferencesQuery.isError ? (
                <div className="ms-page-error">
                  Unable to load notification settings.
                  <button type="button" className="ms-button ms-button--compact" onClick={() => void preferencesQuery.refetch()}>Retry</button>
                </div>
              ) : null}
              <div className="ms-settings-list">
                <SettingsToggle
                  title="Allow notifications"
                  description="Receive message and call alerts on registered devices."
                  checked={preferencesQuery.data?.push_enabled !== false}
                  disabled={preferencesQuery.isLoading || preferencesQuery.isError || preferenceMutation.isPending}
                  onChange={(checked) => preferenceMutation.mutate({ area: "notifications", patch: { push_enabled: checked } })}
                />
                <SettingsToggle
                  title="Show message previews"
                  description="Include message text in notifications. Turn this off for more privacy."
                  checked={preferencesQuery.data?.message_preview_enabled !== false}
                  disabled={preferencesQuery.isLoading || preferencesQuery.isError || preferenceMutation.isPending}
                  onChange={(checked) => preferenceMutation.mutate({ area: "notifications", patch: { message_preview_enabled: checked } })}
                />
                <SettingsToggle
                  title="Pause all notifications"
                  description="Temporarily stop all message and call alerts."
                  checked={Boolean(preferencesQuery.data?.mute_all)}
                  disabled={preferencesQuery.isLoading || preferencesQuery.isError || preferenceMutation.isPending}
                  onChange={(checked) => preferenceMutation.mutate({ area: "notifications", patch: { mute_all: checked } })}
                />
              </div>
              <SectionStatus
                pending={preferenceMutation.isPending && preferenceArea === "notifications"}
                message={preferenceArea === "notifications" ? preferenceMessage : null}
                error={preferenceArea === "notifications" ? preferenceError : null}
              />

              <div className="ms-settings-subsection">
                <div className="ms-settings-subsection__header">
                  <div>
                    <h3>This browser</h3>
                    <p>
                      {!webPushState.supported
                        ? "This browser does not support system notifications."
                        : !webPushState.configured
                          ? "Browser notifications are not available in this deployment."
                          : getWebPushPermissionMessage(webPushState.permission as NotificationPermission)}
                    </p>
                  </div>
                  <div className="ms-page-actions ms-page-actions--wrap">
                    {currentWebPushToken ? (
                      <button type="button" className="ms-button ms-button--compact" onClick={() => { setConfirmationError(null); setConfirmation({ kind: "notification-device", token: currentWebPushToken, label: "This browser", current: true }); }}>
                        Turn off here
                      </button>
                    ) : (
                      <button type="button" className="ms-button ms-button--primary ms-button--compact" disabled={!webPushState.supported || !webPushState.configured || registerWebPushMutation.isPending} onClick={() => registerWebPushMutation.mutate()}>
                        {registerWebPushMutation.isPending ? "Enabling…" : "Enable on this browser"}
                      </button>
                    )}
                  </div>
                </div>
                {pushError ? <div className="ms-page-error">{pushError}</div> : null}
                {pushMessage ? <div className="ms-page-success">{pushMessage}</div> : null}
              </div>

              {chatDevicesQuery.isError ? (
                <div className="ms-page-error">
                  Unable to load your other notification devices.
                  <button type="button" className="ms-button ms-button--compact" onClick={() => void chatDevicesQuery.refetch()}>Retry</button>
                </div>
              ) : null}

              {otherPushDevices.length ? (
                <div className="ms-settings-subsection">
                  <div className="ms-settings-subsection__header">
                    <div>
                      <h3>Other notification devices</h3>
                      <p>Disable notifications on devices you no longer use.</p>
                    </div>
                  </div>
                  <div className="ms-settings-list">
                    {otherPushDevices.map((device, index) => {
                      const baseLabel = describeNotificationDevice(device.platform);
                      const samePlatformCount = otherPushDevices.filter((item) => item.platform === device.platform).length;
                      const samePlatformIndex = otherPushDevices.slice(0, index + 1).filter((item) => item.platform === device.platform).length;
                      const label = `${baseLabel}${samePlatformCount > 1 ? ` ${samePlatformIndex}` : ""}`;
                      return (
                        <div key={device.id} className="ms-settings-row">
                          <div className="ms-settings-row__copy">
                            <strong>{label}</strong>
                            <div className="muted">Last active {formatRelativeActivity(device.last_seen_at)}</div>
                          </div>
                          <div className="ms-page-actions">
                            <button type="button" className="ms-button ms-button--compact ms-button--danger-text" onClick={() => { setConfirmationError(null); setConfirmation({ kind: "notification-device", token: device.push_token, label, current: false }); }}>
                              Disable
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ) : null}
            </section>

            <section id="calling" className="ms-page-surface ms-page-surface--padded ms-settings-card">
              <div className="ms-section-header">
                <div className="ms-section-header__copy">
                  <div className="ms-section-header__eyebrow">Calls</div>
                  <h2>Call quality</h2>
                  <p>Choose how the app balances clarity, stability, and data use.</p>
                </div>
              </div>
              {preferencesQuery.isError ? (
                <div className="ms-page-error">
                  Unable to load call quality settings.
                  <button type="button" className="ms-button ms-button--compact" onClick={() => void preferencesQuery.refetch()}>Retry</button>
                </div>
              ) : null}
              <div className="ms-settings-choice-grid" role="radiogroup" aria-label="Call quality">
                {CALL_QUALITY_OPTIONS.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    role="radio"
                    aria-checked={activePreset === option.value}
                    disabled={preferencesQuery.isLoading || preferencesQuery.isError || preferenceMutation.isPending}
                    className={`ms-settings-choice ${activePreset === option.value ? "is-active" : ""}`}
                    onClick={() => preferenceMutation.mutate({ area: "calls", patch: { call_quality_preference: option.value } })}
                  >
                    <strong>{option.label}</strong>
                    <span>{option.description}</span>
                  </button>
                ))}
              </div>
              <SectionStatus
                pending={preferenceMutation.isPending && preferenceArea === "calls"}
                message={preferenceArea === "calls" ? preferenceMessage : null}
                error={preferenceArea === "calls" ? preferenceError : null}
              />
            </section>

            <section id="devices" className="ms-page-surface ms-page-surface--padded ms-settings-card">
              <div className="ms-section-header">
                <div className="ms-section-header__copy">
                  <div className="ms-section-header__eyebrow">Encryption</div>
                  <h2>Secure devices</h2>
                  <p>These devices can read your end-to-end encrypted conversations.</p>
                </div>
              </div>
              {!e2eeEnvironment.available ? (
                <div className="ms-page-warning">
                  <strong>Secure connection required</strong>
                  <div>{e2eeEnvironment.message}</div>
                  <div>Open Crescentsphere over HTTPS in a supported browser to register this device.</div>
                </div>
              ) : null}
              {e2eeEnvironment.available && e2eeIdentityQuery.isLoading ? <div className="ms-settings-empty">Securing this browser…</div> : null}
              {e2eeEnvironment.available && e2eeIdentityQuery.isError ? (
                <div className="ms-page-error">
                  This browser could not finish secure-device setup.
                  <button type="button" className="ms-button ms-button--compact" onClick={() => void e2eeIdentityQuery.refetch()}>Retry</button>
                </div>
              ) : null}
              {e2eeDevicesQuery.isLoading ? <div className="ms-settings-empty">Loading secure devices…</div> : null}
              {e2eeDevicesQuery.isError ? (
                <div className="ms-page-error">
                  Unable to load secure devices.
                  <button type="button" className="ms-button ms-button--compact" onClick={() => void e2eeDevicesQuery.refetch()}>Retry</button>
                </div>
              ) : null}
              <div className="ms-settings-list">
                {secureDevices.map((device, index) => {
                  const current = device.key_id === currentE2EEIdentity?.keyId;
                  const label = current ? "This browser" : (device.label && device.label !== "This browser" ? device.label : `Secure device ${index + 1}`);
                  return (
                    <div key={device.id || device.key_id} className="ms-settings-row">
                      <div className="ms-settings-row__copy">
                        <div className="ms-settings-row__title">
                          <strong>{label}</strong>
                          {current ? <span className="ms-settings-badge">Current</span> : null}
                        </div>
                        <div className="muted">Last active {formatRelativeActivity(device.last_seen_at)}</div>
                        {device.fingerprint ? (
                          <details className="ms-settings-advanced">
                            <summary>View security code</summary>
                            <code>{formatFingerprint(device.fingerprint)}</code>
                          </details>
                        ) : null}
                      </div>
                      {!current ? (
                        <div className="ms-page-actions">
                          <button type="button" className="ms-button ms-button--compact ms-button--danger-text" onClick={() => { setConfirmationError(null); setConfirmation({ kind: "secure-device", id: device.id || device.key_id, label }); }}>
                            Remove
                          </button>
                        </div>
                      ) : null}
                    </div>
                  );
                })}
                {!e2eeDevicesQuery.isLoading && !e2eeDevicesQuery.isError && !secureDevices.length ? <div className="ms-settings-empty">No secure devices are registered yet.</div> : null}
              </div>
              <SectionStatus message={deviceMessage} error={deviceError} />
            </section>

            <section id="data" className="ms-page-surface ms-page-surface--padded ms-settings-card ms-settings-card--danger">
              <div className="ms-section-header">
                <div className="ms-section-header__copy">
                  <div className="ms-section-header__eyebrow">Your data</div>
                  <h2>Export or delete account</h2>
                  <p>Download a copy of your account data or permanently delete the account.</p>
                </div>
              </div>
              <div className="ms-settings-list">
                <div className="ms-settings-row">
                  <div className="ms-settings-row__copy">
                    <strong>Download your data</strong>
                    <div className="muted">Download a copy of your profile, settings, and stored account records.</div>
                  </div>
                  <div className="ms-page-actions">
                    <button type="button" className="ms-button ms-button--compact" disabled={exportMutation.isPending} onClick={() => exportMutation.mutate()}>
                      {exportMutation.isPending ? "Preparing…" : "Download data"}
                    </button>
                  </div>
                </div>
                <div className="ms-settings-danger-panel">
                  <div className="ms-settings-row__copy">
                    <strong>Delete account permanently</strong>
                    <div id="delete-account-help" className="muted">This cannot be undone. Type your username and current password before continuing.</div>
                    <label className="ms-settings-confirmation">
                      <span>Type <strong>{user?.username}</strong></span>
                      <input value={deleteConfirmation} onChange={(event) => setDeleteConfirmation(event.target.value)} autoComplete="off" aria-label="Username confirmation" aria-describedby="delete-account-help" />
                    </label>
                    <label className="ms-settings-confirmation">
                      <span>Current password</span>
                      <input value={deletePassword} onChange={(event) => setDeletePassword(event.target.value)} type="password" autoComplete="current-password" aria-describedby={deleteError ? "delete-account-error" : "delete-account-help"} />
                    </label>
                    {deleteError ? <div id="delete-account-error" className="ms-page-error" role="alert">{deleteError}</div> : null}
                  </div>
                  <div className="ms-page-actions">
                    <button
                      type="button"
                      className="ms-button ms-button--danger"
                      disabled={deleteMutation.isPending || !deletePassword || !deleteConfirmationMatches}
                      onClick={() => { setConfirmationError(null); setConfirmation({ kind: "delete-account" }); }}
                    >
                      Delete account
                    </button>
                  </div>
                </div>
              </div>
              <SectionStatus message={dataMessage} error={dataError} />
            </section>
          </aside>
        </div>
      </div>

      <ConfirmDialog
        open={Boolean(confirmation)}
        title={confirmationView.title}
        description={confirmationView.description}
        confirmLabel={confirmationView.confirmLabel}
        tone={confirmationView.tone}
        pending={confirmationPending}
        error={confirmationError}
        onConfirm={() => void handleConfirmation()}
        onClose={() => {
          if (confirmationPending) return;
          setConfirmation(null);
          setConfirmationError(null);
        }}
      />
    </div>
  );
}
