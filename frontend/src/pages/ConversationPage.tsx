import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { authApi } from "../api/auth";
import { chatApi, normalizeConversation, normalizeMessage, type MessagePage } from "../api/chat";
import { ForwardMessageModal } from "../components/ForwardMessageModal";
import { ConversationDetailsPanel } from "../components/ConversationDetailsPanel";
import { ConversationList, conversationDisplayName } from "../components/ConversationList";
import { MessageBubble } from "../components/MessageBubble";
import { MessageComposer } from "../components/MessageComposer";
import { uploadPolicyFromCapabilities, validateComposerUpload } from "../components/composer/uploadPolicy";
import { TypingIndicator } from "../components/TypingIndicator";
import { MediaPreviewModal } from "../components/MediaPreviewModal";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { ChatHeader, type ChatHeaderNotice } from "../components/conversation/ChatHeader";
import { useAuth } from "../contexts/AuthContext";
import {
  decryptMessageTextResult,
  encryptAttachmentForConversation,
  encryptMessageForConversation,
  ensureE2EEIdentity,
  establishConversationTrustOnFirstUse,
  getConversationEncryptionReadiness,
  getE2EEErrorMessage,
  markConversationDevicesSeen,
  rewrapAttachmentEncryptionForConversation,
} from "../lib/e2ee";
import { useChatSocket } from "../hooks/useChatSocket";
import { useConversationTimeline } from "../hooks/useConversationTimeline";
import { safeId } from "../lib/safeId";
import { buildConversationDraftKey, buildLegacyConversationDraftKey } from "../lib/conversationDrafts";
import {
  findMessageInPages,
  flattenMessagePages,
  mapMessagePages,
  markMessageDeletedPages,
  removeMessagePages,
  upsertMessagePages,
} from "../lib/messageTimeline";
import { isSameUserIdentity } from "../lib/userIdentity";
import { findActiveCallForConversation, findActiveCallForUser } from "../lib/callLifecycle";
import { getCallMediaErrorMessage, preflightCallMedia } from "../lib/mediaPermissions";
import { patchCallCaches } from "../lib/realtimeCache";
import { mergeConversationReceipts, mergeParticipantReceipts } from "../lib/messageReceipts";
import { conversationPath, isNamedConversationRoute } from "../lib/conversationRoute";
import { personPresenceText } from "../lib/personPresentation";
import { createLocalAttachmentPreview, generateAndStoreLocalPreview, storeLocalPreview, transferLocalPreview } from "../lib/mediaPreviewCache";
import type { UserSearchResult } from "../types/auth";
import type { AttachmentEncryptionEnvelope, Conversation, Message, MessageAttachment } from "../types/chat";

function isSameDay(a: string, b: string) {
  return new Date(a).toDateString() === new Date(b).toDateString();
}

function isGrouped(previous: Message | undefined, current: Message | undefined) {
  if (!previous || !current) return false;
  if (previous.sender.id !== current.sender.id) return false;
  if (!isSameDay(previous.created_at, current.created_at)) return false;
  const elapsed = new Date(current.created_at).getTime() - new Date(previous.created_at).getTime();
  return Number.isFinite(elapsed) && elapsed >= 0 && elapsed < 5 * 60 * 1000;
}

type MessageGroupPosition = "single" | "start" | "middle" | "end";

function getMessageGroupPosition(groupedBefore: boolean, groupedAfter: boolean): MessageGroupPosition {
  if (groupedBefore && groupedAfter) return "middle";
  if (groupedBefore) return "end";
  if (groupedAfter) return "start";
  return "single";
}

function JumpToLatestIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="m6 9 6 6 6-6" />
      <path d="m6 4 6 6 6-6" />
    </svg>
  );
}

function buildOptimisticMessage(
  userId: string,
  username: string,
  text: string,
  attachmentIds: string[],
  replyTo: Message | null,
  clientTempId: string,
  optimisticAttachments: MessageAttachment[] = [],
  options: { isEncrypted?: boolean; type?: string; isVoiceNote?: boolean; durationSeconds?: number | string | null; waveform?: number[] } = {},
) {
  const now = new Date().toISOString();
  return {
    id: `temp-${clientTempId}`,
    client_temp_id: clientTempId,
    type: options.type || (attachmentIds.length ? "file" : "text"),
    text,
    sender: { id: userId, username, display_name: username },
    created_at: now,
    attachments: attachmentIds.map((id) => optimisticAttachments.find((attachment) => attachment.id === id) || { id, original_name: "Attachment", mime_type: "", size: 0 }),
    reactions: [],
    reaction_summary: {},
    links: Array.from(text.matchAll(/https?:\/\/\S+/g)).map((match) => match[0]),
    reply_preview: replyTo ? { id: replyTo.id, text: replyTo.text || "Attachment / voice note" } : null,
    delivery_status: "pending",
    failed_reason: null,
    retry_count: 0,
    is_encrypted: Boolean(options.isEncrypted),
    voice_note: options.isVoiceNote
      ? { is_voice_note: true, duration_seconds: options.durationSeconds ?? null, waveform: options.waveform ?? [] }
      : null,
  } as Message;
}

function markConversationReadInCache(messagesKeyConversationId: string, queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.setQueryData(["conversations"], (current: Message[] | unknown) => {
    if (!Array.isArray(current)) return current;
    return current.map((conversation) => {
      if (!conversation || typeof conversation !== "object") return conversation;
      const item = conversation as Record<string, unknown>;
      if (String(item.id || "") !== messagesKeyConversationId) return conversation;
      return { ...item, unread_count: 0 };
    });
  });
  queryClient.setQueryData(["conversation", messagesKeyConversationId], (current: unknown) => {
    if (!current || typeof current !== "object") return current;
    return { ...(current as Record<string, unknown>), unread_count: 0 };
  });
}

function applyParticipantReceiptInCache(
  conversationId: string,
  eventName: "message.read" | "message.delivered",
  data: Record<string, unknown>,
  queryClient: ReturnType<typeof useQueryClient>,
) {
  const receiptUserId = String(data.user_id || "");
  if (!receiptUserId) return;

  const patchConversation = (value: unknown) => {
    if (!value || typeof value !== "object") return value;
    const conversation = value as Record<string, unknown>;
    if (String(conversation.id || "") !== conversationId) return value;
    const participants = Array.isArray(conversation.participants) ? conversation.participants : [];
    return {
      ...conversation,
      participants: participants.map((participant) => {
        if (!participant || typeof participant !== "object") return participant;
        const item = participant as Record<string, unknown>;
        const participantUser = item.user && typeof item.user === "object" ? item.user as Record<string, unknown> : {};
        if (String(participantUser.id || "") !== receiptUserId) return participant;

        return mergeParticipantReceipts(item as unknown as Conversation["participants"][number], {
          last_delivered_message: String(data.last_delivered_message_id || "") || undefined,
          last_delivered_at: String(data.last_delivered_at || "") || undefined,
          last_read_message: eventName === "message.read" ? String(data.last_read_message_id || "") || undefined : undefined,
          last_read_at: eventName === "message.read" ? String(data.last_read_at || "") || undefined : undefined,
        });
      }),
    };
  };

  queryClient.setQueryData(["conversation", conversationId], patchConversation);
  queryClient.setQueryData(["conversations"], (current: unknown) => {
    if (!Array.isArray(current)) return current;
    return current.map(patchConversation);
  });
}


function patchConversationViewerState(
  conversation: Conversation | undefined,
  conversationId: string,
  userId: string,
  field: "is_pinned" | "is_muted" | "is_archived",
  value: boolean,
) {
  if (!conversation || String(conversation.id) !== String(conversationId)) return conversation;
  return {
    ...conversation,
    participants: conversation.participants.map((participant) =>
      String(participant.user.id) === String(userId) ? { ...participant, [field]: value } : participant,
    ),
  };
}

function getErrorMessage(error: unknown, fallback = "Something went wrong.") {
  if (error && typeof error === "object" && "response" in error) {
    const response = (error as { response?: { data?: unknown } }).response;
    const data = response?.data;
    if (data && typeof data === "object") {
      const detail = (data as Record<string, unknown>).detail;
      const call = (data as Record<string, unknown>).call;
      if (typeof detail === "string") return detail;
      if (Array.isArray(call) && call.length) return String(call[0]);
      if (typeof call === "string") return call;
    }
  }
  return error instanceof Error ? error.message : fallback;
}

function getActiveCallIdFromError(error: unknown) {
  if (!error || typeof error !== "object" || !("response" in error)) return "";
  const data = (error as { response?: { data?: unknown } }).response?.data;
  if (!data || typeof data !== "object") return "";
  return String((data as Record<string, unknown>).active_call_id || "");
}


type TimelineConfirmation =
  | { kind: "delete-message"; message: Message }
  | { kind: "report-message"; message: Message }
  | { kind: "delete-conversation"; title: string; isGroup: boolean }
  | { kind: "leave-conversation" }
  | { kind: "block-contact"; userId: string; displayName: string };

const INBOX_MIN_WIDTH = 280;
const INBOX_MAX_WIDTH = 440;
const INBOX_DEFAULT_WIDTH = 340;
const DETAILS_MIN_WIDTH = 300;
const DETAILS_MAX_WIDTH = 440;
const DETAILS_DEFAULT_WIDTH = 340;
const CHAT_MIN_WIDTH = 420;

function clampInboxWidth(width: number) {
  return Math.min(INBOX_MAX_WIDTH, Math.max(INBOX_MIN_WIDTH, Math.round(width)));
}

function readStoredInboxWidth() {
  if (typeof window === "undefined") return INBOX_DEFAULT_WIDTH;
  const stored = Number(window.localStorage.getItem("chat-inbox-width"));
  return Number.isFinite(stored) ? clampInboxWidth(stored) : INBOX_DEFAULT_WIDTH;
}

function clampDetailsWidth(width: number, inboxWidth = INBOX_DEFAULT_WIDTH) {
  const viewportMax = typeof window === "undefined"
    ? DETAILS_MAX_WIDTH
    : Math.max(DETAILS_MIN_WIDTH, window.innerWidth - inboxWidth - CHAT_MIN_WIDTH);
  const maxWidth = Math.min(DETAILS_MAX_WIDTH, viewportMax);
  return Math.min(maxWidth, Math.max(DETAILS_MIN_WIDTH, Math.round(width)));
}

function readStoredDetailsWidth() {
  if (typeof window === "undefined") return DETAILS_DEFAULT_WIDTH;
  const stored = Number(window.localStorage.getItem("chat-details-width"));
  return Number.isFinite(stored) ? clampDetailsWidth(stored) : DETAILS_DEFAULT_WIDTH;
}

export function ConversationPage() {
  const { conversationId: routeConversationKey = "" } = useParams();
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const navigate = useNavigate();
  const namedRoute = isNamedConversationRoute(routeConversationKey);
  const routeConversationQuery = useQuery({
    queryKey: ["conversation-route", routeConversationKey.toLowerCase()],
    queryFn: () => chatApi.getConversationByRoute(routeConversationKey),
    enabled: namedRoute,
    initialData: () => {
      if (!namedRoute) return undefined;
      const routeName = routeConversationKey.replace(/^@/, "").toLowerCase();
      return (queryClient.getQueryData<Conversation[]>(["conversations"]) ?? []).find(
        (conversation) => conversation.type === "group"
          ? conversation.slug?.toLowerCase() === routeName
          : conversation.participants.some((participant) => !isSameUserIdentity(participant.user, user) && participant.user.username?.toLowerCase() === routeName),
      );
    },
    staleTime: 5 * 60_000,
    retry: 1,
  });
  const conversationId = namedRoute ? routeConversationQuery.data?.id || "" : routeConversationKey;
  const { socket, socketStatus } = useChatSocket();
  const [replyTo, setReplyTo] = useState<Message | null>(null);
  const [editingMessage, setEditingMessage] = useState<Message | null>(null);
  const [forwardMessage, setForwardMessage] = useState<Message | null>(null);
  const [typingUsers, setTypingUsers] = useState<Record<string, string>>({});
  const [showRealtimeNotice, setShowRealtimeNotice] = useState(false);
  const typingTimeoutRef = useRef<number | null>(null);
  const typingActiveRef = useRef(false);
  const typingExpiryTimersRef = useRef<Record<string, number>>({});
  const [showDetails, setShowDetails] = useState(() => typeof window === "undefined" || window.innerWidth > 1180);
  const [mediaKind, setMediaKind] = useState<"all" | "image" | "video" | "audio" | "file">("all");
  const [previewAttachmentId, setPreviewAttachmentId] = useState<string | null>(null);
  const [callError, setCallError] = useState<string | null>(null);
  const [conversationStateError, setConversationStateError] = useState<string | null>(null);
  const [conversationStatePending, setConversationStatePending] = useState<"pin" | "mute" | "archive" | null>(null);
  const [startingCallType, setStartingCallType] = useState<"voice" | "video" | null>(null);
  const [decryptedTexts, setDecryptedTexts] = useState<Record<string, string>>({});
  const [decryptionStates, setDecryptionStates] = useState<Record<string, { status: "pending" | "ready" | "unavailable" | "error"; message?: string }>>({});
  const [initiallyReadyConversationId, setInitiallyReadyConversationId] = useState("");
  const [reportedMessageIds, setReportedMessageIds] = useState<Record<string, boolean>>({});
  const [inboxWidth, setInboxWidth] = useState(readStoredInboxWidth);
  const [isResizingInbox, setIsResizingInbox] = useState(false);
  const [detailsWidth, setDetailsWidth] = useState(readStoredDetailsWidth);
  const [isResizingDetails, setIsResizingDetails] = useState(false);
  const [pageVisible, setPageVisible] = useState(() => typeof document === "undefined" || document.visibilityState === "visible");
  const [messageActionErrors, setMessageActionErrors] = useState<Record<string, string>>({});
  const [messageActionPending, setMessageActionPending] = useState<Record<string, boolean>>({});
  const reactionPendingRef = useRef(new Set<string>());
  const [confirmation, setConfirmation] = useState<TimelineConfirmation | null>(null);
  const [confirmationPending, setConfirmationPending] = useState(false);
  const [confirmationError, setConfirmationError] = useState<string | null>(null);
  const encryptedAttachmentUploadsRef = useRef<Record<string, AttachmentEncryptionEnvelope>>({});
  const failedSendPayloadsRef = useRef<Record<string, Record<string, unknown>>>({});
  const decryptionCiphertextRef = useRef<Record<string, string>>({});
  const lastReadReceiptMessageRef = useRef("");
  const lastDeliveredReceiptMessageRef = useRef("");
  const timelineAtLatestRef = useRef(true);
  const conversationViewRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const node = conversationViewRef.current;
    const viewport = window.visualViewport;
    if (!node || !viewport) return;

    let frame = 0;
    const syncVisualViewport = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        if (window.matchMedia("(max-width: 900px)").matches) {
          node.style.setProperty("--ms-chat-visual-viewport-height", `${Math.round(viewport.height)}px`);
        } else {
          node.style.removeProperty("--ms-chat-visual-viewport-height");
        }
      });
    };

    syncVisualViewport();
    viewport.addEventListener("resize", syncVisualViewport);
    viewport.addEventListener("scroll", syncVisualViewport);
    window.addEventListener("orientationchange", syncVisualViewport);
    return () => {
      window.cancelAnimationFrame(frame);
      viewport.removeEventListener("resize", syncVisualViewport);
      viewport.removeEventListener("scroll", syncVisualViewport);
      window.removeEventListener("orientationchange", syncVisualViewport);
      node.style.removeProperty("--ms-chat-visual-viewport-height");
    };
  }, []);

  useEffect(() => {
    lastReadReceiptMessageRef.current = "";
    lastDeliveredReceiptMessageRef.current = "";
    setMessageActionErrors({});
    setMessageActionPending({});
    setConfirmation(null);
    setConfirmationError(null);
    setConversationStateError(null);
    setConversationStatePending(null);
    return () => {
      if (typingTimeoutRef.current) {
        window.clearTimeout(typingTimeoutRef.current);
        typingTimeoutRef.current = null;
      }
      if (typingActiveRef.current && conversationId && socket.isOpen()) {
        socket.send({ event: "typing.stop", data: { conversation_id: conversationId } });
      }
      typingActiveRef.current = false;
      Object.values(typingExpiryTimersRef.current).forEach((timer) => window.clearTimeout(timer));
      typingExpiryTimersRef.current = {};
      setTypingUsers({});
    };
  }, [conversationId, socket]);

  useEffect(() => {
    if (socketStatus === "open") {
      setShowRealtimeNotice(false);
      return;
    }
    const timer = window.setTimeout(() => setShowRealtimeNotice(true), 3500);
    return () => window.clearTimeout(timer);
  }, [socketStatus]);

  useEffect(() => {
    if (socketStatus === "open") return;
    Object.values(typingExpiryTimersRef.current).forEach((timer) => window.clearTimeout(timer));
    typingExpiryTimersRef.current = {};
    setTypingUsers({});
    typingActiveRef.current = false;
  }, [socketStatus]);

  useEffect(() => {
    const stopTypingWhenHidden = () => {
      if (document.visibilityState !== "hidden" || !typingActiveRef.current || !conversationId) return;
      if (typingTimeoutRef.current) {
        window.clearTimeout(typingTimeoutRef.current);
        typingTimeoutRef.current = null;
      }
      if (socket.isOpen()) socket.send({ event: "typing.stop", data: { conversation_id: conversationId } });
      typingActiveRef.current = false;
    };
    document.addEventListener("visibilitychange", stopTypingWhenHidden);
    return () => document.removeEventListener("visibilitychange", stopTypingWhenHidden);
  }, [conversationId, socket]);

  useEffect(() => {
    const updateVisibility = () => setPageVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", updateVisibility);
    return () => document.removeEventListener("visibilitychange", updateVisibility);
  }, []);

  const conversationQuery = useQuery({
    queryKey: ["conversation", conversationId],
    queryFn: () => chatApi.getConversation(conversationId),
    enabled: !!conversationId,
    staleTime: 60_000,
    gcTime: 30 * 60_000,
    refetchOnWindowFocus: false,
    structuralSharing: (current, incoming) => mergeConversationReceipts(current as Conversation | undefined, incoming as Conversation),
  });
  useEffect(() => {
    const conversation = conversationQuery.data || routeConversationQuery.data;
    if (!conversation) return;
    const canonicalPath = conversationPath(conversation, user);
    if (window.location.pathname !== canonicalPath) {
      queryClient.setQueryData(["conversation-route", canonicalPath.slice("/chat/".length).toLowerCase()], conversation);
      navigate(canonicalPath, { replace: true });
    }
  }, [conversationQuery.data, navigate, queryClient, routeConversationQuery.data, user]);
  const conversationsQuery = useQuery({
    queryKey: ["conversations"],
    queryFn: ({ signal }) => chatApi.listConversations(signal),
    staleTime: 30_000,
    gcTime: 30 * 60_000,
    refetchOnWindowFocus: false,
  });
  const friendsQuery = useQuery({
    queryKey: ["friend-requests", "friends"],
    queryFn: ({ signal }) => authApi.listFriendRequests("friends", signal),
  });
  const recentCallsQuery = useQuery({ queryKey: ["recent-calls"], queryFn: ({ signal }) => chatApi.listCalls(undefined, signal), staleTime: 15_000, retry: 1, refetchOnWindowFocus: false });
  const capabilitiesQuery = useQuery({
    queryKey: ["chat-capabilities"],
    queryFn: chatApi.getCapabilities,
    staleTime: 30 * 60 * 1000,
  });
  const notificationsQuery = useQuery({ queryKey: ["conversation-notifications", conversationId], queryFn: () => chatApi.getConversationNotifications(conversationId), enabled: !!conversationId });
  const mediaQuery = useQuery({ queryKey: ["conversation-media", conversationId, mediaKind], queryFn: ({ signal }) => chatApi.listConversationMedia(conversationId, mediaKind, signal), enabled: !!conversationId });
  const allMediaQuery = useQuery({ queryKey: ["conversation-media", conversationId, "all"], queryFn: ({ signal }) => chatApi.listConversationMedia(conversationId, "all", signal), enabled: !!conversationId });
  const messagesQuery = useInfiniteQuery({
    queryKey: ["messages", conversationId],
    queryFn: ({ pageParam, signal }) => chatApi.listMessages(conversationId, typeof pageParam === "string" ? pageParam : null, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next || undefined,
    enabled: !!conversationId,
    staleTime: 5 * 60_000,
    gcTime: 30 * 60_000,
    refetchOnWindowFocus: false,
  });
  const e2eeIdentityQuery = useQuery({
    queryKey: ["e2ee-identity", String(user?.id || "")],
    queryFn: () => ensureE2EEIdentity(String(user?.id || "")),
    enabled: Boolean(user?.id),
    staleTime: 60_000,
  });
  const e2eeMaterialQuery = useQuery({
    queryKey: ["conversation-e2ee", conversationId],
    queryFn: () => chatApi.getConversationE2EEKeys(conversationId),
    enabled: !!conversationId && !!user?.id,
    staleTime: 10_000,
    retry: 2,
  });
  useEffect(() => {
    if (!conversationId || !e2eeIdentityQuery.data?.keyId) return;
    // Identity registration and key-material loading can finish in either order.
    // Refetch once the local key is known so a newly linked or rotated browser
    // does not remain stuck in "registering" with an earlier material response.
    void e2eeMaterialQuery.refetch();
  }, [conversationId, e2eeIdentityQuery.data?.keyId]);
  const localCurrentUser = useMemo(
    () => (conversationQuery.data?.participants ?? []).find((participant) => isSameUserIdentity(participant.user, user))?.user ?? user,
    [conversationQuery.data?.participants, user],
  );
  const currentUserIdentity = useMemo(
    () => ({
      id: localCurrentUser?.id ?? user?.id,
      username: localCurrentUser?.username ?? user?.username,
      email: localCurrentUser?.email ?? user?.email,
      display_name: localCurrentUser?.display_name ?? user?.profile?.display_name ?? user?.display_name,
    }),
    [localCurrentUser, user],
  );
  const friends = useMemo<UserSearchResult[]>(() => {
    const currentUserId = String(user?.id || "");
    const seen = new Set<string>();
    return (friendsQuery.data ?? [])
      .map((request) => String(request.from_user.id) === currentUserId ? request.to_user : request.from_user)
      .filter((friend) => {
        const id = String(friend.id || "");
        if (!id || id === currentUserId || seen.has(id)) return false;
        seen.add(id);
        return true;
      });
  }, [friendsQuery.data, user?.id]);
  const conversationParticipantIds = useMemo(
    () => (conversationQuery.data?.participants ?? [])
      .filter((participant) => !participant.left_at && !participant.banned_at)
      .map((participant) => String(participant.user.id))
      .filter(Boolean),
    [conversationQuery.data?.participants],
  );
  const composerUploadPolicy = useMemo(() => {
    const policy = uploadPolicyFromCapabilities(capabilitiesQuery.data);
    // AES-GCM adds a small authentication tag. Reserve enough room so a file
    // accepted by the composer cannot become slightly larger than the server
    // limit after client-side encryption.
    return { ...policy, maxBytes: Math.max(1, policy.maxBytes - 64) };
  }, [capabilitiesQuery.data]);
  const encryptionReadiness = useMemo(
    () => getConversationEncryptionReadiness({
      material: e2eeMaterialQuery.data,
      participantUserIds: conversationParticipantIds,
      currentUserId: String(user?.id || ""),
      currentKeyId: e2eeIdentityQuery.data?.keyId,
      isLoading: e2eeIdentityQuery.isLoading || e2eeMaterialQuery.isLoading || conversationQuery.isLoading,
      isError: e2eeIdentityQuery.isError || e2eeMaterialQuery.isError,
    }),
    [
      conversationParticipantIds,
      conversationQuery.isLoading,
      e2eeIdentityQuery.data?.keyId,
      e2eeIdentityQuery.isError,
      e2eeIdentityQuery.isLoading,
      e2eeMaterialQuery.data,
      e2eeMaterialQuery.isError,
      e2eeMaterialQuery.isLoading,
      user?.id,
    ],
  );
  const onlineFriendMutation = useMutation({
    mutationFn: async (person: UserSearchResult) => {
      const existing = (conversationsQuery.data ?? []).find(
        (conversation) => conversation.type === "direct"
          && conversation.participants.some((participant) => String(participant.user.id) === String(person.id)),
      );
      return existing ?? chatApi.createDirectConversation(person.id);
    },
    onMutate: () => setConversationStateError(null),
    onSuccess: (conversation) => {
      queryClient.setQueryData(["conversation", conversation.id], conversation);
      queryClient.setQueryData<Conversation[]>(["conversations"], (current = []) => {
        const next = current.filter((item) => item.id !== conversation.id);
        return [conversation, ...next];
      });
      navigate(conversationPath(conversation, user));
    },
    onError: (error) => setConversationStateError(getErrorMessage(error, "Unable to open this conversation.")),
  });

  const sendMutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) => chatApi.sendMessage(conversationId, payload),
    onMutate: async (payload) => {
      await queryClient.cancelQueries({ queryKey: ["messages", conversationId] });
      const currentData = queryClient.getQueryData<InfiniteData<MessagePage>>(["messages", conversationId]);
      const clientTempId = String(payload.client_temp_id || safeId("message"));
      failedSendPayloadsRef.current[clientTempId] = { ...payload, client_temp_id: clientTempId };
      if (user?.id) {
        const optimisticText = String((payload._optimistic_text as string | undefined) ?? payload.text ?? "");
        const replyToId = String(payload.reply_to_id || "");
        const optimisticReply = replyToId ? findMessageInPages(currentData, replyToId) ?? null : null;
        const optimisticAttachments = Array.isArray(payload._optimistic_attachments)
          ? payload._optimistic_attachments as MessageAttachment[]
          : [];
        const optimistic = buildOptimisticMessage(
          String(user.id),
          user.username || "You",
          optimisticText,
          (payload.attachment_ids as string[] | undefined) ?? [],
          optimisticReply,
          clientTempId,
          optimisticAttachments,
          {
            isEncrypted: Boolean(payload.is_encrypted),
            type: String(payload.type || (Array.isArray(payload.attachment_ids) && payload.attachment_ids.length ? "file" : "text")),
            isVoiceNote: Boolean(payload.is_voice_note),
            durationSeconds: typeof payload.duration_seconds === "number" || typeof payload.duration_seconds === "string" ? payload.duration_seconds : null,
            waveform: Array.isArray(payload.waveform) ? payload.waveform.map(Number).filter(Number.isFinite) : [],
          },
        );
        queryClient.setQueryData<InfiniteData<MessagePage>>(
          ["messages", conversationId],
          (current) => upsertMessagePages(current, optimistic),
        );
        if (Boolean(payload.is_encrypted) && optimisticText.trim()) {
          setDecryptedTexts((current) => ({ ...current, [optimistic.id]: optimisticText }));
          setDecryptionStates((current) => ({ ...current, [optimistic.id]: { status: "ready" } }));
        }
      }
      return { clientTempId };
    },
    onError: (error, payload, context) => {
      const clientTempId = context?.clientTempId || String(payload?.client_temp_id || "");
      queryClient.setQueryData<InfiniteData<MessagePage>>(
        ["messages", conversationId],
        (current) => mapMessagePages(
          current,
          (item) => item.client_temp_id === clientTempId,
          (item) => ({
            ...item,
            delivery_status: "failed",
            failed_reason: getE2EEErrorMessage(error, "Failed to send. Tap retry."),
            retry_count: (item.retry_count ?? 0) + 1,
          }),
        ),
      );
    },
    onSuccess: async (message, payload, context) => {
      const optimisticText = String((payload._optimistic_text as string | undefined) ?? payload.text ?? "");
      if (context?.clientTempId) delete failedSendPayloadsRef.current[context.clientTempId];
      if (message.is_encrypted && optimisticText.trim()) {
        if (message.encryption?.ciphertext) {
          decryptionCiphertextRef.current[message.id] = message.encryption.ciphertext;
        }
        setDecryptedTexts((current) => {
          const next = { ...current, [message.id]: optimisticText };
          if (context?.clientTempId) delete next[`temp-${context.clientTempId}`];
          return next;
        });
        setDecryptionStates((current) => {
          const next = { ...current, [message.id]: { status: "ready" as const } };
          if (context?.clientTempId) delete next[`temp-${context.clientTempId}`];
          return next;
        });
      }
      const sentAttachmentIds = Array.isArray(payload.attachment_ids) ? payload.attachment_ids.map((value) => String(value)) : [];
      if (user?.id && sentAttachmentIds.length && message.attachments?.length) {
        await Promise.allSettled(sentAttachmentIds.map((uploadId, index) => {
          const target = message.attachments[index];
          const envelope = encryptedAttachmentUploadsRef.current[uploadId];
          if (!target || !envelope) return Promise.resolve(false);
          return transferLocalPreview(String(user.id), {
            id: uploadId,
            original_name: target.original_name,
            mime_type: target.mime_type,
            media_kind: target.media_kind,
            size: target.size,
            is_encrypted: true,
            encryption: envelope,
          }, target);
        }));
      }
      sentAttachmentIds.forEach((attachmentId) => { delete encryptedAttachmentUploadsRef.current[attachmentId]; });
      if (!payload._is_retry) setReplyTo(null);
      queryClient.setQueryData<InfiniteData<MessagePage>>(
        ["messages", conversationId],
        (current) => upsertMessagePages(current, message),
      );
      if (payload.is_encrypted || payload.attachment_encryption) {
        await queryClient.invalidateQueries({ queryKey: ["conversation-e2ee", conversationId] });
      }
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  const acknowledgeConversationRead = useCallback((targetConversationId: string, messageId: string) => {
    if (!targetConversationId || !messageId) return;
    const receiptKey = `${targetConversationId}:${messageId}`;
    if (lastReadReceiptMessageRef.current === receiptKey) return;
    lastReadReceiptMessageRef.current = receiptKey;
    void chatApi.markConversationRead(targetConversationId, { message_id: messageId }).then(() => {
      markConversationReadInCache(targetConversationId, queryClient);
    }).catch(() => {
      if (lastReadReceiptMessageRef.current === receiptKey) lastReadReceiptMessageRef.current = "";
    });
  }, [queryClient]);

  useEffect(() => {
    if (!conversationId) return;
    socket.subscribeToConversation(conversationId);
    const unsubscribe = socket.subscribe((payload) => {
      const data = (payload.data ?? {}) as Record<string, unknown>;
      const payloadConversationId = String(data.conversation || data.conversation_id || "");
      if (payload.event === "e2ee.keys.updated") {
        if (!payloadConversationId || payloadConversationId === conversationId) {
          void queryClient.invalidateQueries({ queryKey: ["conversation-e2ee", conversationId] });
          void queryClient.invalidateQueries({ queryKey: ["e2ee-devices"] });
        }
        return;
      }
      if (payloadConversationId && payloadConversationId !== conversationId) return;

      if (payload.event === "message.sent") {
        const clientTempId = String(data.client_temp_id || "");
        const messageId = String(data.message_id || "");
        if (clientTempId) {
          delete failedSendPayloadsRef.current[clientTempId];
          queryClient.setQueryData<InfiniteData<MessagePage>>(["messages", conversationId], (current) => mapMessagePages(
            current,
            (item) => item.client_temp_id === clientTempId,
            (item) => ({ ...item, id: messageId || item.id, delivery_status: "sent", failed_reason: null }),
          ));
        }
        return;
      }

      if (payload.event === "message.created" || payload.event === "message.updated") {
        const normalized = normalizeMessage(data);
        queryClient.setQueryData<InfiniteData<MessagePage>>(["messages", conversationId], (current) => {
          const next = upsertMessagePages(current, normalized);
          const replyTargetId = normalized.reply_preview?.id;
          return replyTargetId
            ? mapMessagePages(next, (message) => message.id === replyTargetId, (message) => ({
                ...message,
                can_edit: false,
                edit_locked_reason: "This message can no longer be edited because it has replies.",
              }))
            : next;
        });
        if (
          payload.event === "message.created"
          && document.visibilityState === "visible"
          && timelineAtLatestRef.current
          && !isSameUserIdentity(normalized.sender, localCurrentUser)
        ) {
          acknowledgeConversationRead(conversationId, normalized.id);
        }
        void queryClient.invalidateQueries({ queryKey: ["conversations"] });
        return;
      }

      if (payload.event === "message.reaction_updated") {
        const normalized = normalizeMessage(data);
        queryClient.setQueryData<InfiniteData<MessagePage>>(["messages", conversationId], (current) => mapMessagePages(
          current,
          (message) => message.id === normalized.id,
          (message) => ({
            ...message,
            reactions: normalized.reactions,
            reaction_summary: normalized.reaction_summary,
            can_edit: normalized.can_edit,
            edit_locked_reason: normalized.edit_locked_reason,
          }),
        ));
        return;
      }

      if (payload.event === "message.deleted") {
        const deletedId = String(data.id || data.message_id || "");
        queryClient.setQueryData<InfiniteData<MessagePage>>(["messages", conversationId], (current) => markMessageDeletedPages(current, deletedId));
        delete decryptionCiphertextRef.current[deletedId];
        setDecryptedTexts((current) => {
          if (!(deletedId in current)) return current;
          const next = { ...current };
          delete next[deletedId];
          return next;
        });
        setDecryptionStates((current) => {
          if (!(deletedId in current)) return current;
          const next = { ...current };
          delete next[deletedId];
          return next;
        });
        void queryClient.invalidateQueries({ queryKey: ["conversations"] });
        return;
      }

      if (payload.event === "message.read" || payload.event === "message.delivered") {
        const receiptEvent = payload.event as "message.read" | "message.delivered";
        applyParticipantReceiptInCache(conversationId, receiptEvent, data, queryClient);

        if (receiptEvent === "message.read" && String(data.user_id || "") === String(user?.id || "")) {
          markConversationReadInCache(conversationId, queryClient);
        }

        // Opening a chat on another participant's device only changes receipt
        // pointers. Updating those pointers in cache avoids refetching the entire
        // timeline, so media and messages never enter a loading state for the
        // participant who already has the conversation open.
        return;
      }

      if (payload.event === "conversation.updated") {
        const updated = normalizeConversation(data);
        if (updated.id === conversationId) {
          queryClient.setQueryData<Conversation>(["conversation", conversationId], (current) => mergeConversationReceipts(current, updated));
          queryClient.setQueryData(["conversations"], (current: unknown) => Array.isArray(current)
            ? current.map((item) => item && typeof item === "object" && String((item as Record<string, unknown>).id || "") === conversationId
              ? mergeConversationReceipts(item as Conversation, updated)
              : item)
            : current);
        }
        return;
      }

      if ((payload.event === "typing.started" || payload.event === "typing.stopped") && payloadConversationId === conversationId) {
        const typingUserId = String(data.user_id || "");
        if (!typingUserId || isSameUserIdentity({ id: typingUserId, username: String(data.username || ""), display_name: String(data.display_name || "") }, localCurrentUser)) return;
        if (typingExpiryTimersRef.current[typingUserId]) {
          window.clearTimeout(typingExpiryTimersRef.current[typingUserId]);
          delete typingExpiryTimersRef.current[typingUserId];
        }
        const removeTypingUser = () => {
          setTypingUsers((current) => {
            if (!current[typingUserId]) return current;
            const next = { ...current };
            delete next[typingUserId];
            return next;
          });
          delete typingExpiryTimersRef.current[typingUserId];
        };
        if (payload.event === "typing.stopped") {
          removeTypingUser();
          return;
        }
        const typingName = String(data.display_name || data.username || "Someone");
        setTypingUsers((current) => ({ ...current, [typingUserId]: typingName }));
        const expiresAt = Date.parse(String(data.expires_at || ""));
        const delay = Number.isFinite(expiresAt) ? Math.max(500, Math.min(7000, expiresAt - Date.now())) : 6500;
        typingExpiryTimersRef.current[typingUserId] = window.setTimeout(removeTypingUser, delay);
      }
    });
    return () => {
      socket.unsubscribeFromConversation(conversationId);
      unsubscribe();
    };
  }, [acknowledgeConversationRead, conversationId, socket, queryClient, user?.id, localCurrentUser]);

  const pagedMessages = useMemo(() => flattenMessagePages(messagesQuery.data), [messagesQuery.data]);

  useEffect(() => {
    if (!user?.id || !e2eeIdentityQuery.data || !pagedMessages.length) return;
    let cancelled = false;
    const encryptedMessages = pagedMessages.filter((message) => message.is_encrypted && message.encryption);
    if (!encryptedMessages.length) return;

    const changedMessageIds: string[] = [];
    for (const message of encryptedMessages) {
      const ciphertext = String(message.encryption?.ciphertext || "");
      if (decryptionCiphertextRef.current[message.id] !== ciphertext) {
        decryptionCiphertextRef.current[message.id] = ciphertext;
        changedMessageIds.push(message.id);
      }
    }
    if (changedMessageIds.length) {
      setDecryptedTexts((current) => {
        const next = { ...current };
        changedMessageIds.forEach((messageId) => { delete next[messageId]; });
        return next;
      });
    }
    setDecryptionStates((current) => {
      const next = { ...current };
      for (const message of encryptedMessages) {
        if (!next[message.id] || changedMessageIds.includes(message.id)) {
          next[message.id] = { status: "pending" };
        }
      }
      return next;
    });

    void (async () => {
      const nextEntries = await Promise.all(
        encryptedMessages.map(async (message) => [
          message.id,
          await decryptMessageTextResult(String(user.id), message, e2eeIdentityQuery.data),
        ] as const),
      );
      if (cancelled) return;
      setDecryptedTexts((current) => {
        const next = { ...current };
        for (const [messageId, result] of nextEntries) {
          if (result.status === "ready") next[messageId] = result.text;
          else delete next[messageId];
        }
        return next;
      });
      setDecryptionStates((current) => {
        const next = { ...current };
        for (const [messageId, result] of nextEntries) {
          next[messageId] = { status: result.status, message: result.message };
        }
        return next;
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [e2eeIdentityQuery.data, pagedMessages, user?.id]);

  const hasEncryptedMessagesPending = useMemo(
    () => pagedMessages.some((message) => {
      if (!message.is_encrypted || !message.encryption) return false;
      const state = decryptionStates[message.id];
      return !e2eeIdentityQuery.isError && (!state || state.status === "pending");
    }),
    [decryptionStates, e2eeIdentityQuery.isError, pagedMessages],
  );

  useEffect(() => {
    if (!conversationId || messagesQuery.isLoading || conversationQuery.isLoading || routeConversationQuery.isLoading) return;
    if (encryptionReadiness.status === "preparing" || hasEncryptedMessagesPending) return;
    setInitiallyReadyConversationId(conversationId);
  }, [conversationId, conversationQuery.isLoading, encryptionReadiness.status, hasEncryptedMessagesPending, messagesQuery.isLoading, routeConversationQuery.isLoading]);

  const messages = useMemo(
    () => {
      if (initiallyReadyConversationId !== conversationId) return [];
      return [...pagedMessages]
        .map((message) => {
          if (!message.is_encrypted) return message;
          const state = decryptionStates[message.id]
            ?? (!message.encryption || e2eeIdentityQuery.isError
              ? { status: "error" as const, message: "This encrypted message could not be opened on this device." }
              : { status: "pending" as const });
          return {
            ...message,
            text: decryptedTexts[message.id] ?? "",
            decryption_state: state.status,
            decryption_message: state.message,
          };
        })
        .filter((message) => message.decryption_state !== "pending")
        .sort((a, b) => {
          const timestampDelta = new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
          return timestampDelta || String(a.id).localeCompare(String(b.id));
        });
    },
    [conversationId, decryptedTexts, decryptionStates, e2eeIdentityQuery.isError, initiallyReadyConversationId, pagedMessages],
  );
  const latestMessageId = useMemo(() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      const status = String(message.delivery_status || "").toLowerCase();
      if (!message.id.startsWith("temp-") && status !== "sending" && status !== "failed") return message.id;
    }
    return "";
  }, [messages]);
  const {
    scrollerRef,
    showJumpToLatest,
    timelineAtLatest,
    replyJumpMessageId,
    timelineNotice,
    highlightedMessageId,
    setTimelineNotice,
    jumpToMessage,
    scrollToLatest,
    registerMessageRef,
  } = useConversationTimeline({
    conversationId,
    messages,
    queryClient,
    hasNextPage: Boolean(messagesQuery.hasNextPage),
    isFetchingNextPage: messagesQuery.isFetchingNextPage,
    pageCount: messagesQuery.data?.pages.length ?? 0,
    fetchNextPage: () => messagesQuery.fetchNextPage(),
    getErrorMessage,
  });
  timelineAtLatestRef.current = timelineAtLatest;

  useEffect(() => {
    if (!conversationId || !latestMessageId) return;
    const receiptKey = `${conversationId}:${latestMessageId}`;
    if (lastDeliveredReceiptMessageRef.current === receiptKey) return;
    lastDeliveredReceiptMessageRef.current = receiptKey;
    void chatApi.markConversationDelivered(conversationId, { message_id: latestMessageId }).catch(() => {
      if (lastDeliveredReceiptMessageRef.current === receiptKey) lastDeliveredReceiptMessageRef.current = "";
    });
  }, [conversationId, latestMessageId]);

  useEffect(() => {
    if (!conversationId || !latestMessageId || !pageVisible || !timelineAtLatest) return;
    acknowledgeConversationRead(conversationId, latestMessageId);
  }, [acknowledgeConversationRead, conversationId, latestMessageId, pageVisible, timelineAtLatest]);
  const encryptionReadinessMessage = useMemo(() => {
    if (encryptionReadiness.code !== "participant_device_missing" || !encryptionReadiness.missingParticipantIds.length) {
      return encryptionReadiness.message;
    }
    const missingNames = (conversationQuery.data?.participants ?? [])
      .filter((participant) => encryptionReadiness.missingParticipantIds.includes(String(participant.user.id)))
      .map((participant) => participant.user.display_name || participant.user.username)
      .filter(Boolean);
    if (!missingNames.length) return encryptionReadiness.message;
    if (missingNames.length === 1) return `${missingNames[0]} needs to finish secure-device setup before messages can be sent.`;
    return `${missingNames.slice(0, 2).join(" and ")} need to finish secure-device setup before messages can be sent.`;
  }, [conversationQuery.data?.participants, encryptionReadiness]);
  const composerDisabledReason = encryptionReadiness.status === "blocked" ? encryptionReadinessMessage : null;
  const previewAttachments = useMemo(() => messages.flatMap((message) => message.attachments || []).filter((attachment) => !attachment.view_once), [messages]);
  const previewAttachmentIndex = useMemo(() => previewAttachments.findIndex((attachment) => attachment.id === previewAttachmentId), [previewAttachments, previewAttachmentId]);
  const previewAttachment = previewAttachmentIndex >= 0 ? previewAttachments[previewAttachmentIndex] : null;
  const previewMessage = useMemo(
    () => messages.find((message) => (message.attachments || []).some((attachment) => attachment.id === previewAttachmentId)) ?? null,
    [messages, previewAttachmentId],
  );
  useEffect(() => {
    if (!user?.id || !e2eeMaterialQuery.data) return;
    const currentUserId = String(user.id);
    establishConversationTrustOnFirstUse(currentUserId, e2eeMaterialQuery.data);
    markConversationDevicesSeen(currentUserId, e2eeMaterialQuery.data);
  }, [e2eeMaterialQuery.data, user?.id]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") {
        event.preventDefault();
        const element = document.getElementById("conversation-sidebar-search") as HTMLInputElement | null;
        element?.focus();
        element?.select();
      }
      if (event.key === "Escape") {
        setPreviewAttachmentId(null);
        setShowDetails(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const unreadBoundary = useMemo(() => {
    const unreadCount = Math.max(0, conversationQuery.data?.unread_count ?? 0);
    if (!unreadCount || unreadCount >= messages.length) return unreadCount >= messages.length ? 0 : -1;
    return messages.length - unreadCount;
  }, [conversationQuery.data?.unread_count, messages.length]);

  const sendTyping = useCallback(() => {
    if (!conversationId || !socket.isOpen()) return;
    if (!typingActiveRef.current) {
      socket.send({ event: "typing.start", data: { conversation_id: conversationId } });
      typingActiveRef.current = true;
    }
    if (typingTimeoutRef.current) window.clearTimeout(typingTimeoutRef.current);
    typingTimeoutRef.current = window.setTimeout(() => {
      typingTimeoutRef.current = null;
      if (typingActiveRef.current && socket.isOpen()) {
        socket.send({ event: "typing.stop", data: { conversation_id: conversationId } });
      }
      typingActiveRef.current = false;
    }, 1600);
  }, [conversationId, socket]);

  const setMessageActionError = (messageId: string, error?: string | null) => {
    setMessageActionErrors((current) => {
      const next = { ...current };
      if (error) next[messageId] = error;
      else delete next[messageId];
      return next;
    });
  };

  const setMessagePending = (messageId: string, pending: boolean) => {
    setMessageActionPending((current) => {
      const next = { ...current };
      if (pending) next[messageId] = true;
      else delete next[messageId];
      return next;
    });
  };

  const toggleReaction = async (message: Message, emoji: string) => {
    if (reactionPendingRef.current.has(message.id)) return;
    const previousData = queryClient.getQueryData<InfiniteData<MessagePage>>(["messages", conversationId]);
    const previousMessage = findMessageInPages(previousData, message.id);
    const currentUserId = String(user?.id || "");
    const reacted = (message.reactions ?? []).some((reaction) => reaction.emoji === emoji && String(reaction.user.id) === currentUserId);

    setMessageActionError(message.id, null);
    reactionPendingRef.current.add(message.id);
    queryClient.setQueryData<InfiniteData<MessagePage>>(
      ["messages", conversationId],
      (current) => mapMessagePages(current, (item) => item.id === message.id, (item) => {
        const reactionSummary = { ...(item.reaction_summary ?? {}) };
        const previousUserReaction = (item.reactions ?? []).find((reaction) => String(reaction.user.id) === currentUserId);
        if (previousUserReaction?.emoji) {
          const previousCount = Math.max(0, (reactionSummary[previousUserReaction.emoji] ?? 0) - 1);
          if (previousCount === 0) delete reactionSummary[previousUserReaction.emoji];
          else reactionSummary[previousUserReaction.emoji] = previousCount;
        }
        if (!reacted) reactionSummary[emoji] = (reactionSummary[emoji] ?? 0) + 1;
        const reactions = reacted
          ? (item.reactions ?? []).filter((reaction) => String(reaction.user.id) !== currentUserId)
          : [...(item.reactions ?? []).filter((reaction) => String(reaction.user.id) !== currentUserId), {
              id: `optimistic-${item.id}-${emoji}`,
              emoji,
              user: {
                id: currentUserId,
                username: user?.username || "You",
                display_name: user?.full_name || user?.profile?.display_name || user?.username || "You",
              },
              created_at: new Date().toISOString(),
            }];
        return {
          ...item,
          reaction_summary: reactionSummary,
          reactions,
          can_edit: reacted ? item.can_edit : false,
          edit_locked_reason: reacted ? item.edit_locked_reason : "This message can no longer be edited because someone reacted to it.",
        };
      }),
    );

    try {
      const updated = reacted
        ? await chatApi.removeReaction(message.id, emoji)
        : await chatApi.reactToMessage(message.id, emoji);
      queryClient.setQueryData<InfiniteData<MessagePage>>(
        ["messages", conversationId],
        (current) => mapMessagePages(
          current,
          (item) => item.id === updated.id,
          (item) => ({
            ...item,
            reactions: updated.reactions,
            reaction_summary: updated.reaction_summary,
            can_edit: updated.can_edit,
            edit_locked_reason: updated.edit_locked_reason,
            edit_deadline: updated.edit_deadline,
          }),
        ),
      );
    } catch (error) {
      if (previousMessage) {
        queryClient.setQueryData<InfiniteData<MessagePage>>(
          ["messages", conversationId],
          (current) => mapMessagePages(current, (item) => item.id === message.id, () => previousMessage),
        );
      }
      setMessageActionError(message.id, getErrorMessage(error, "Could not update this reaction."));
    } finally {
      reactionPendingRef.current.delete(message.id);
    }
  };

  const setLocalMessageFailure = (message: Message, reason: string) => {
    queryClient.setQueryData<InfiniteData<MessagePage>>(
      ["messages", conversationId],
      (current) => mapMessagePages(
        current,
        (item) => item.id === message.id || Boolean(message.client_temp_id && item.client_temp_id === message.client_temp_id),
        (item) => ({ ...item, delivery_status: "failed", failed_reason: reason }),
      ),
    );
  };

  const refreshFailedSecurePayload = async (payload: Record<string, unknown>) => {
    if (!user?.id) throw new Error("Sign in again before retrying this message.");
    if (!encryptionReadiness.canEncrypt) throw new Error(encryptionReadinessMessage);

    const nextPayload: Record<string, unknown> = { ...payload };
    const plaintext = String(payload._optimistic_text || "");
    if (plaintext.trim()) {
      nextPayload.text = "";
      nextPayload.is_encrypted = true;
      nextPayload.encryption = await encryptMessageForConversation({
        userId: String(user.id),
        conversationId,
        plaintext,
        participantUserIds: conversationParticipantIds,
      });
    }

    const encryptedAttachments = Array.isArray(payload.attachment_encryption)
      ? payload.attachment_encryption.filter((entry): entry is Record<string, unknown> => Boolean(entry && typeof entry === "object"))
      : [];
    if (encryptedAttachments.length) {
      nextPayload.attachment_encryption = await Promise.all(encryptedAttachments.map(async (entry) => {
        const uploadId = String(entry.upload_id || "");
        const { upload_id: _uploadId, ...envelopePayload } = entry;
        const envelope = await rewrapAttachmentEncryptionForConversation({
          userId: String(user.id),
          conversationId,
          envelope: envelopePayload as unknown as AttachmentEncryptionEnvelope,
          participantUserIds: conversationParticipantIds,
        });
        return { upload_id: uploadId, ...envelope };
      }));
    }
    return nextPayload;
  };

  const handleRetry = async (message: Message) => {
    if (messageActionPending[message.id]) return;
    setMessageActionError(message.id, null);
    setMessagePending(message.id, true);
    try {
      const clientTempId = String(message.client_temp_id || "");
      const failedPayload = clientTempId ? failedSendPayloadsRef.current[clientTempId] : undefined;
      if (failedPayload) {
        const refreshedPayload = await refreshFailedSecurePayload(failedPayload);
        await sendMutation.mutateAsync({ ...refreshedPayload, _is_retry: true });
        return;
      }
      if (message.id.startsWith("temp-")) {
        throw new Error("This unsent message is no longer available for secure retry. Copy its text and send it again.");
      }
      const updated = await chatApi.retryMessage(message.id);
      queryClient.setQueryData<InfiniteData<MessagePage>>(
        ["messages", conversationId],
        (current) => upsertMessagePages(current, updated, { insertWhenMissing: false }),
      );
    } catch (error) {
      const reason = getE2EEErrorMessage(error, "This message could not be retried.");
      setLocalMessageFailure(message, reason);
      setMessageActionError(message.id, reason);
    } finally {
      setMessagePending(message.id, false);
    }
  };

  const handleSaveMessage = async (payload: Record<string, unknown>) => {
    if (!user?.id) throw new Error("Sign in again before sending a message.");
    if (!encryptionReadiness.canEncrypt) throw new Error(encryptionReadinessMessage);

    if (editingMessage) {
      const plaintext = String(payload.text || "");
      const envelope = await encryptMessageForConversation({
        userId: String(user.id),
        conversationId,
        plaintext,
        participantUserIds: conversationParticipantIds,
      });
      const updated = await chatApi.editMessage(editingMessage.id, {
        text: "",
        is_encrypted: true,
        encryption: envelope,
      });
      if (updated.encryption?.ciphertext) {
        decryptionCiphertextRef.current[updated.id] = updated.encryption.ciphertext;
      }
      setDecryptedTexts((current) => ({ ...current, [updated.id]: plaintext }));
      setDecryptionStates((current) => ({ ...current, [updated.id]: { status: "ready" } }));
      queryClient.setQueryData<InfiniteData<MessagePage>>(["messages", conversationId], (current) => upsertMessagePages(current, updated, { insertWhenMissing: false }));
      setEditingMessage(null);
      return;
    }

    const attachmentIds = Array.isArray(payload.attachment_ids) ? payload.attachment_ids.map((entry) => String(entry)) : [];
    const plaintext = typeof payload.text === "string" ? String(payload.text) : "";
    const clientTempId = String(payload.client_temp_id || safeId("message"));
    const currentData = queryClient.getQueryData<InfiniteData<MessagePage>>(["messages", conversationId]);
    const optimisticAttachments = Array.isArray(payload._optimistic_attachments) ? payload._optimistic_attachments as MessageAttachment[] : [];
    const optimistic = buildOptimisticMessage(
      String(user.id),
      user.username || "You",
      plaintext,
      attachmentIds,
      payload.reply_to_id ? findMessageInPages(currentData, String(payload.reply_to_id)) ?? null : null,
      clientTempId,
      optimisticAttachments,
      { isEncrypted: Boolean(plaintext.trim()), type: String(payload.type || (attachmentIds.length ? "file" : "text")) },
    );
    queryClient.setQueryData<InfiniteData<MessagePage>>(["messages", conversationId], (current) => upsertMessagePages(current, optimistic));
    if (plaintext.trim()) {
      setDecryptedTexts((current) => ({ ...current, [optimistic.id]: plaintext }));
      setDecryptionStates((current) => ({ ...current, [optimistic.id]: { status: "ready" } }));
    }

    let mutationStarted = false;
    try {
      const attachmentEncryption = await Promise.all(attachmentIds.map(async (attachmentId) => {
        const storedEnvelope = encryptedAttachmentUploadsRef.current[attachmentId];
        if (!storedEnvelope) throw new Error("A secure attachment is not ready. Remove it and upload it again.");
        const envelope = await rewrapAttachmentEncryptionForConversation({
          userId: String(user.id),
          conversationId,
          envelope: storedEnvelope,
          participantUserIds: conversationParticipantIds,
        });
        encryptedAttachmentUploadsRef.current[attachmentId] = envelope;
        return { upload_id: attachmentId, ...envelope };
      }));
      const nextPayload: Record<string, unknown> = {
        ...payload,
        client_temp_id: clientTempId,
        attachment_encryption: attachmentEncryption.length ? attachmentEncryption : undefined,
        _optimistic_attachments: attachmentIds.map((attachmentId, index) => {
          const source = optimisticAttachments.find((item) => String(item.id || "") === attachmentId);
          return {
            ...(source || { id: attachmentId, original_name: "Attachment", mime_type: "application/octet-stream", size: 0 }),
            id: attachmentId,
            is_encrypted: false,
            encryption: encryptedAttachmentUploadsRef.current[attachmentId] || attachmentEncryption[index],
          } satisfies MessageAttachment;
        }),
      };
      if (plaintext.trim()) {
        nextPayload.text = "";
        nextPayload.is_encrypted = true;
        nextPayload.encryption = await encryptMessageForConversation({
          userId: String(user.id),
          conversationId,
          plaintext,
          participantUserIds: conversationParticipantIds,
        });
        nextPayload._optimistic_text = plaintext;
      }
      mutationStarted = true;
      await sendMutation.mutateAsync(nextPayload);
    } catch (error) {
      if (!mutationStarted) {
        queryClient.setQueryData<InfiniteData<MessagePage>>(
          ["messages", conversationId],
          (current) => removeMessagePages(current, (message) => message.id === optimistic.id),
        );
        setDecryptedTexts((current) => {
          const next = { ...current };
          delete next[optimistic.id];
          return next;
        });
        setDecryptionStates((current) => {
          const next = { ...current };
          delete next[optimistic.id];
          return next;
        });
      }
      throw error;
    }
  };

  const uploadConversationAttachment = async (
    file: File,
    metadata?: { original_name?: string; mime_type?: string },
    options?: { signal?: AbortSignal; onProgress?: (progress: number) => void },
  ) => {
    if (!user?.id) throw new Error("Sign in again before uploading a file.");
    if (!encryptionReadiness.canEncrypt) throw new Error(encryptionReadinessMessage);
    const originalName = metadata?.original_name || file.name;
    const effectiveMimeType = metadata?.mime_type
      || file.type
      || (originalName.toLowerCase().endsWith(".pdf") ? "application/pdf" : "application/octet-stream");
    const sourceFile = file.type === effectiveMimeType
      ? file
      : new File([file], file.name, { type: effectiveMimeType, lastModified: file.lastModified });
    const validation = validateComposerUpload(sourceFile, composerUploadPolicy);
    if (!validation.valid) throw new Error(validation.message || "This file cannot be uploaded.");
    if (options?.signal?.aborted) throw new DOMException("Upload cancelled", "AbortError");
    const sourceMediaKind = effectiveMimeType.startsWith("video/")
      ? "video"
      : effectiveMimeType.startsWith("image/")
        ? "image"
        : effectiveMimeType === "application/pdf" || originalName.toLowerCase().endsWith(".pdf")
          ? "pdf"
          : effectiveMimeType.startsWith("audio/")
            ? "audio"
            : "";
    const encryptedPreview = await createLocalAttachmentPreview(sourceFile, sourceMediaKind).catch(() => null);
    const encrypted = await encryptAttachmentForConversation({
      userId: String(user.id),
      conversationId,
      file: sourceFile,
      participantUserIds: conversationParticipantIds,
      previewBlob: encryptedPreview,
    });
    if (options?.signal?.aborted) throw new DOMException("Upload cancelled", "AbortError");
    const upload = await chatApi.uploadFile(encrypted.uploadFile, {
      original_name: originalName,
      mime_type: effectiveMimeType,
      signal: options?.signal,
      onProgress: options?.onProgress,
      metadata_source_file: sourceFile,
      include_thumbnail: false,
    });
    if (options?.signal?.aborted) throw new DOMException("Upload cancelled", "AbortError");
    encryptedAttachmentUploadsRef.current[upload.id] = encrypted.envelope;
    const localAttachment = {
      id: upload.id,
      original_name: originalName,
      mime_type: effectiveMimeType,
      media_kind: sourceMediaKind || null,
      size: sourceFile.size,
      is_encrypted: true,
      encryption: encrypted.envelope,
    };
    const previewTask = encryptedPreview
      ? storeLocalPreview(String(user.id), localAttachment, encryptedPreview)
      : upload.localThumbnail
      ? storeLocalPreview(String(user.id), localAttachment, upload.localThumbnail)
      : generateAndStoreLocalPreview(String(user.id), localAttachment, sourceFile);
    void previewTask.catch(() => undefined);
    return { ...upload, encryptedPreview };
  };

  const participantNames = useMemo(
    () => (conversationQuery.data?.participants ?? []).filter((participant) => !isSameUserIdentity(participant.user, user)).map((participant) => participant.user.display_name || participant.user.username),
    [conversationQuery.data?.participants, user],
  );
  const messageIndexMap = useMemo(() => new Map(messages.map((message, index) => [message.id, index])), [messages]);
  const participantReceiptState = useMemo(
    () => (conversationQuery.data?.participants ?? []).filter((participant) => !isSameUserIdentity(participant.user, user)),
    [conversationQuery.data?.participants, user],
  );

  const chatTitle = useMemo(() => {
    const conversation = conversationQuery.data;
    if (!conversation) return "Conversation";
    return conversationDisplayName(conversation, String(user?.id || ""), currentUserIdentity);
  }, [conversationQuery.data, currentUserIdentity, user?.id]);

  const chatPeer = useMemo(
    () => conversationQuery.data?.type === "direct"
      ? conversationQuery.data.participants.find((participant) => !isSameUserIdentity(participant.user, user))?.user ?? null
      : null,
    [conversationQuery.data, user],
  );
  const chatSubtitle = useMemo(() => {
    const conversation = conversationQuery.data;
    if (!conversation) return "Conversation";
    if (conversation.type === "group") return `${conversation.participants.length} participants`;
    return personPresenceText(chatPeer);
  }, [chatPeer, conversationQuery.data]);
  const isGroupConversation = conversationQuery.data?.type === "group";

  const getReadByNames = (message: Message) => {
    const raw = message.metadata?.read_by;
    if (Array.isArray(raw)) {
      const names = raw.map((item) => typeof item === "string" ? item : typeof item === "object" && item && "display_name" in item ? String((item as Record<string, unknown>).display_name || (item as Record<string, unknown>).username || "") : "").filter(Boolean);
      if (names.length) return names;
    }
    const messageIndex = messageIndexMap.get(message.id);
    if (messageIndex === undefined) return String(message.delivery_status).toLowerCase() === "read" ? participantNames : [];
    return participantReceiptState
      .filter((participant) => {
        const readIndex = participant.last_read_message ? messageIndexMap.get(participant.last_read_message) : undefined;
        return readIndex !== undefined && readIndex >= messageIndex;
      })
      .map((participant) => participant.user.display_name || participant.user.username);
  };

  const getDeliveredByNames = (message: Message) => {
    const fromDeliveries = (message.deliveries ?? [])
      .filter((delivery) => String(delivery.user.id) !== String(user?.id || ""))
      .map((delivery) => delivery.user.display_name || delivery.user.username)
      .filter(Boolean);
    if (fromDeliveries.length) return fromDeliveries;
    const messageIndex = messageIndexMap.get(message.id);
    if (messageIndex === undefined) return [];
    return participantReceiptState
      .filter((participant) => {
        const deliveredIndex = participant.last_delivered_message ? messageIndexMap.get(participant.last_delivered_message) : undefined;
        return deliveredIndex !== undefined && deliveredIndex >= messageIndex;
      })
      .map((participant) => participant.user.display_name || participant.user.username);
  };

  const getOwnDeliveryStatus = (message: Message) => {
    if (String(message.delivery_status || "").toLowerCase() === "failed") return "failed";
    if (getReadByNames(message).length) return "read";
    if (getDeliveredByNames(message).length) return "delivered";
    return message.delivery_status || "sent";
  };

  const confirmationContent = useMemo(() => {
    if (!confirmation) return null;
    if (confirmation.kind === "delete-message") {
      const isLocalOnly = confirmation.message.id.startsWith("temp-");
      return {
        title: isLocalOnly ? "Remove this unsent message?" : "Delete this message?",
        description: isLocalOnly
          ? "The failed message and its retry information will be removed from this device."
          : "The message will be removed from this conversation. This action cannot be undone.",
        confirmLabel: isLocalOnly ? "Remove message" : "Delete message",
      };
    }
    if (confirmation.kind === "report-message") {
      return {
        title: "Report this message?",
        description: "The message will be sent to moderation for review. The other participant will not be notified by this screen.",
        confirmLabel: "Report message",
      };
    }
    if (confirmation.kind === "delete-conversation") {
      return {
        title: `Delete ${confirmation.isGroup ? "group" : "chat"}?`,
        description: confirmation.isGroup
          ? `This permanently deletes ${confirmation.title} and all of its messages for every member. This action cannot be undone.`
          : `This permanently deletes the conversation with ${confirmation.title}, including its messages and shared media, for both people. This action cannot be undone.`,
        confirmLabel: "Delete chat",
      };
    }
    if (confirmation.kind === "leave-conversation") {
      return {
        title: "Leave this group?",
        description: "You will stop receiving new messages and may need another invitation to return.",
        confirmLabel: "Leave group",
      };
    }
    return {
      title: `Block ${confirmation.displayName}?`,
      description: "They will no longer be able to message or call you. You can unblock them later from Settings.",
      confirmLabel: "Block contact",
    };
  }, [confirmation]);

  const runConfirmedAction = async () => {
    if (!confirmation || confirmationPending) return;
    const action = confirmation;
    const messageId = action.kind === "delete-message" || action.kind === "report-message" ? action.message.id : "";
    try {
      setConfirmationPending(true);
      setConfirmationError(null);
      if (messageId) {
        setMessageActionError(messageId, null);
        setMessagePending(messageId, true);
      }

      if (action.kind === "delete-message") {
        const isLocalOnly = action.message.id.startsWith("temp-");
        if (isLocalOnly) {
          const clientTempId = String(action.message.client_temp_id || action.message.id.replace(/^temp-/, ""));
          delete failedSendPayloadsRef.current[clientTempId];
          queryClient.setQueryData<InfiniteData<MessagePage>>(
            ["messages", conversationId],
            (current) => removeMessagePages(
              current,
              (message) => message.id === action.message.id || Boolean(action.message.client_temp_id && message.client_temp_id === action.message.client_temp_id),
            ),
          );
        } else {
          await chatApi.deleteMessage(action.message.id);
          queryClient.setQueryData<InfiniteData<MessagePage>>(
            ["messages", conversationId],
            (current) => markMessageDeletedPages(current, action.message.id),
          );
        }
        delete decryptionCiphertextRef.current[action.message.id];
        setDecryptedTexts((current) => {
          const next = { ...current };
          delete next[action.message.id];
          return next;
        });
        setDecryptionStates((current) => {
          const next = { ...current };
          delete next[action.message.id];
          return next;
        });
        if (editingMessage?.id === action.message.id) setEditingMessage(null);
        if (replyTo?.id === action.message.id) setReplyTo(null);
        setTimelineNotice({ tone: "success", message: isLocalOnly ? "Unsent message removed." : "Message deleted." });
      } else if (action.kind === "report-message") {
        if (!reportedMessageIds[action.message.id]) {
          await chatApi.reportMessage(action.message.id, { reason: "user_report", details: "Reported from the conversation UI." });
          setReportedMessageIds((current) => ({ ...current, [action.message.id]: true }));
        }
        setTimelineNotice({ tone: "success", message: "Message reported for review." });
      } else if (action.kind === "leave-conversation") {
        await chatApi.leaveConversation(conversationId);
        await queryClient.invalidateQueries({ queryKey: ["conversations"] });
        navigate("/chat");
      } else if (action.kind === "delete-conversation") {
        await chatApi.deleteConversation(conversationId);
        queryClient.removeQueries({ queryKey: ["conversation", conversationId] });
        queryClient.removeQueries({ queryKey: ["messages", conversationId] });
        await queryClient.invalidateQueries({ queryKey: ["conversations"] });
        navigate("/chat", { replace: true });
      } else {
        await chatApi.blockUser(action.userId);
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ["chat-blocks"] }),
          queryClient.invalidateQueries({ queryKey: ["conversations"] }),
          queryClient.invalidateQueries({ queryKey: ["friend-requests"] }),
          queryClient.invalidateQueries({ queryKey: ["user-search"] }),
          queryClient.invalidateQueries({ queryKey: ["nearby-users"] }),
        ]);
        navigate("/chat");
      }
      setConfirmation(null);
    } catch (error) {
      const message = getErrorMessage(error, "The action could not be completed.");
      setConfirmationError(message);
      if (messageId) setMessageActionError(messageId, message);
    } finally {
      setConfirmationPending(false);
      if (messageId) setMessagePending(messageId, false);
    }
  };

  const startCall = async (callType: "voice" | "video") => {
    if (!conversationId || startingCallType) return;
    const recentCalls = recentCallsQuery.data ?? [];
    const existingConversationCall = findActiveCallForConversation(recentCalls, conversationId, currentUserIdentity);
    if (existingConversationCall) {
      navigate(`/calls/${existingConversationCall.id}`);
      return;
    }
    const otherActiveCall = findActiveCallForUser(recentCalls, currentUserIdentity);
    if (otherActiveCall) {
      setCallError("You already have another call in progress. Return to it before starting a new call.");
      return;
    }

    try {
      setCallError(null);
      setStartingCallType(callType);
      await preflightCallMedia(callType);
      const call = await chatApi.startCall(conversationId, { call_type: callType });
      patchCallCaches(queryClient, call);
      navigate(`/calls/${call.id}`);
    } catch (error) {
      const activeCallId = getActiveCallIdFromError(error);
      if (activeCallId) {
        navigate(`/calls/${activeCallId}`);
      } else if (error instanceof DOMException || (error instanceof Error && /camera|microphone|media|https|permission/i.test(error.message))) {
        setCallError(await getCallMediaErrorMessage(error, callType));
      } else {
        setCallError(getErrorMessage(error, "Could not start the call."));
      }
    } finally {
      setStartingCallType(null);
    }
  };

  useEffect(() => {
    window.localStorage.setItem("chat-inbox-width", String(inboxWidth));
  }, [inboxWidth]);

  useEffect(() => {
    setDetailsWidth((current) => clampDetailsWidth(current, inboxWidth));
  }, [inboxWidth]);

  useEffect(() => {
    const handleResize = () => {
      setInboxWidth((current) => clampInboxWidth(current));
      setDetailsWidth((current) => clampDetailsWidth(current, inboxWidth));
    };
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [inboxWidth]);

  useEffect(() => {
    window.localStorage.setItem("chat-details-width", String(detailsWidth));
  }, [detailsWidth]);

  const startInboxResize = (event: ReactPointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = inboxWidth;
    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    setIsResizingInbox(true);

    const handlePointerMove = (moveEvent: PointerEvent) => {
      setInboxWidth(clampInboxWidth(startWidth + moveEvent.clientX - startX));
    };

    const stopResize = () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", stopResize);
      window.removeEventListener("pointercancel", stopResize);
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
      setIsResizingInbox(false);
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", stopResize, { once: true });
    window.addEventListener("pointercancel", stopResize, { once: true });
  };

  const startDetailsResize = (event: ReactPointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = detailsWidth;
    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    setIsResizingDetails(true);

    const handlePointerMove = (moveEvent: PointerEvent) => {
      setDetailsWidth(clampDetailsWidth(startWidth - (moveEvent.clientX - startX), inboxWidth));
    };

    const stopResize = () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", stopResize);
      window.removeEventListener("pointercancel", stopResize);
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
      setIsResizingDetails(false);
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", stopResize, { once: true });
    window.addEventListener("pointercancel", stopResize, { once: true });
  };

  const shellStyle = {
    "--chat-inbox-width": `${inboxWidth}px`,
    "--chat-details-width": `${detailsWidth}px`,
  } as CSSProperties;
  const conversationError = routeConversationQuery.isError
    ? getErrorMessage(routeConversationQuery.error, "This conversation link is unavailable.")
    : conversationQuery.isError ? getErrorMessage(conversationQuery.error, "Could not load this conversation.") : null;
  const messagesError = messagesQuery.isError ? getErrorMessage(messagesQuery.error, "Could not load messages.") : null;
  const blockingConversationError = conversationError && messages.length === 0 ? conversationError : null;
  const chatError = messagesError || blockingConversationError;
  const isInitialChatLoading = routeConversationQuery.isLoading
    || conversationQuery.isLoading
    || messagesQuery.isLoading
    || initiallyReadyConversationId !== conversationId;
  const showEmptyConversation = !isInitialChatLoading && !chatError && messages.length === 0;

  const headerNotices = useMemo<ChatHeaderNotice[]>(() => {
    const notices: ChatHeaderNotice[] = [];
    if (showRealtimeNotice && socketStatus !== "open") {
      notices.push({
        id: "socket",
        message: `Realtime is ${socketStatus}. Messages still send, but calls and live receipts may wait for reconnection.`,
      });
    }
    if (conversationError && messages.length > 0) {
      notices.push({
        id: "conversation-refresh",
        tone: "warning",
        message: "Conversation details could not refresh. Messages are still available.",
      });
    }
    if (encryptionReadiness.status === "blocked") {
      notices.push({
        id: "e2ee-readiness",
        tone: encryptionReadiness.status === "blocked" ? "danger" : "neutral",
        message: encryptionReadinessMessage,
      });
    }
    if (callError) {
      notices.push({ id: "call-error", tone: "danger", message: callError });
    }
    if (conversationStateError) {
      notices.push({ id: "conversation-state-error", tone: "danger", message: conversationStateError });
    }
    return notices;
  }, [callError, conversationError, conversationStateError, encryptionReadiness.status, encryptionReadinessMessage, messages.length, showRealtimeNotice, socketStatus]);

  return (
    <div ref={conversationViewRef} className={`ms-conversation-view ${showDetails ? "ms-conversation-view--details-open" : ""}`} style={shellStyle}>
      <aside className="ms-conversation-view__inbox" aria-label="Conversations">
        {conversationsQuery.isLoading ? (
          <div className="ms-conversations-state" role="status" aria-live="polite">
            <span className="ms-conversations-state__spinner" aria-hidden="true" />
            <strong>Loading chats</strong>
            <span>Your conversations will appear here.</span>
          </div>
        ) : conversationsQuery.isError ? (
          <div className="ms-conversations-state ms-conversations-state--error" role="alert">
            <strong>Chats could not be loaded</strong>
            <span>{getErrorMessage(conversationsQuery.error, "Check your connection and try again.")}</span>
            <button type="button" onClick={() => void conversationsQuery.refetch()}>Retry</button>
          </div>
        ) : (
          <ConversationList
            conversations={conversationsQuery.data ?? []}
            currentUserId={String(user?.id || "")}
            currentUser={currentUserIdentity}
            variant="sidebar"
            searchInputId="conversation-sidebar-search"
            onlineFriends={friends}
            openingFriendId={onlineFriendMutation.isPending ? String(onlineFriendMutation.variables?.id || "") : null}
            onOpenFriend={(friend) => onlineFriendMutation.mutate(friend)}
          />
        )}
      </aside>

      <button
        type="button"
        className={`ms-conversation-resizer ms-conversation-resizer--inbox ${isResizingInbox ? "is-dragging" : ""}`}
        onPointerDown={startInboxResize}
        onKeyDown={(event) => {
          const step = event.shiftKey ? 32 : 16;
          if (event.key === "ArrowLeft") setInboxWidth((current) => clampInboxWidth(current - step));
          else if (event.key === "ArrowRight") setInboxWidth((current) => clampInboxWidth(current + step));
          else if (event.key === "Home") setInboxWidth(INBOX_MIN_WIDTH);
          else if (event.key === "End") setInboxWidth(INBOX_MAX_WIDTH);
          else return;
          event.preventDefault();
        }}
        role="separator"
        aria-label="Resize conversation list"
        aria-orientation="vertical"
        aria-valuemin={INBOX_MIN_WIDTH}
        aria-valuemax={INBOX_MAX_WIDTH}
        aria-valuenow={inboxWidth}
        aria-keyshortcuts="ArrowLeft ArrowRight Home End"
        title="Use left and right arrow keys to resize the conversation list"
      />

      <section className="ms-chat-surface" aria-label={chatTitle}>
        <ChatHeader
          title={chatTitle}
          subtitle={chatSubtitle}
          avatarPerson={chatPeer ?? { display_name: chatTitle }}
          isGroup={isGroupConversation}
          notices={headerNotices}
          detailsOpen={showDetails}
          startingCallType={startingCallType}
          onBack={() => navigate("/chat")}
          onToggleDetails={() => setShowDetails((current) => !current)}
          onStartVoiceCall={() => void startCall("voice")}
          onStartVideoCall={() => void startCall("video")}
        />

        <section className="ms-chat-timeline" ref={scrollerRef} aria-label="Messages">
          {isInitialChatLoading ? (
            <div className="ms-chat-state">
              <strong>Loading conversation...</strong>
              <span>Fetching messages and conversation details.</span>
            </div>
          ) : null}
          {chatError ? (
            <div className="ms-chat-state ms-chat-state--error">
              <strong>Chat could not load</strong>
              <span>{chatError}</span>
              <div className="button-row">
                {blockingConversationError ? <button type="button" className="ms-button ms-button--ghost ms-button--compact" onClick={() => void (namedRoute ? routeConversationQuery.refetch() : conversationQuery.refetch())}>Retry conversation</button> : null}
                {messagesError ? <button type="button" className="ms-button ms-button--ghost ms-button--compact" onClick={() => void messagesQuery.refetch()}>Retry messages</button> : null}
              </div>
            </div>
          ) : null}
          {showEmptyConversation ? (
            <div className="ms-chat-state">
              <strong>No messages yet</strong>
              <span>Send the first message to start this conversation.</span>
            </div>
          ) : null}
          {!isInitialChatLoading && !chatError && messagesQuery.isFetchingNextPage ? <div className="ms-timeline-chip" role="status">Loading older messages…</div> : null}
          {!isInitialChatLoading && !chatError && messagesQuery.isFetchNextPageError ? (
            <div className="ms-timeline-notice ms-timeline-notice--danger" role="alert">
              <span>Older messages could not load.</span>
              <button type="button" onClick={() => void messagesQuery.fetchNextPage()}>Retry</button>
            </div>
          ) : null}
          {replyJumpMessageId ? <div className="ms-timeline-chip" role="status">Loading original message…</div> : null}
          {timelineNotice ? (
            <div className={`ms-timeline-notice ms-timeline-notice--${timelineNotice.tone}`} role={timelineNotice.tone === "danger" ? "alert" : "status"}>
              <span>{timelineNotice.message}</span>
              <button type="button" aria-label="Dismiss message" onClick={() => setTimelineNotice(null)}>Dismiss</button>
            </div>
          ) : null}
          {!isInitialChatLoading && !chatError && !messagesQuery.hasNextPage && messages.length ? <div className="ms-timeline-chip">Start of conversation</div> : null}
          {messages.map((message, index) => {
            const previous = messages[index - 1];
            const next = messages[index + 1];
            const showDate = !previous || !isSameDay(previous.created_at, message.created_at);
            const groupedBefore = unreadBoundary !== index && isGrouped(previous, message);
            const groupedAfter = unreadBoundary !== index + 1 && isGrouped(message, next);
            const groupPosition = getMessageGroupPosition(groupedBefore, groupedAfter);
            const isOwnMessage = message.call_event?.initiated_by_id
              ? String(message.call_event.initiated_by_id) === String(user?.id || "")
              : isSameUserIdentity(message.sender, user);
            return (
              <div
                key={message.id}
                data-message-id={message.id}
                className={`ms-message-block is-group-${groupPosition} ${groupedBefore ? "is-group-continuation" : ""} ${highlightedMessageId === message.id ? "message-search-active message-reference-active" : ""}`}
                ref={(node) => registerMessageRef(message.id, node)}
              >
                {showDate ? <div className="ms-timeline-chip">{new Date(message.created_at).toLocaleDateString()}</div> : null}
                {unreadBoundary === index ? <div className="ms-timeline-chip ms-timeline-chip--unread">New messages</div> : null}
                <MessageBubble
                  message={message}
                  own={isOwnMessage}
                  groupPosition={groupPosition}
                  readByNames={isOwnMessage && isGroupConversation ? getReadByNames(message) : []}
                  deliveredByNames={isOwnMessage && isGroupConversation ? getDeliveredByNames(message) : []}
                  deliveryStatus={isOwnMessage ? getOwnDeliveryStatus(message) : message.delivery_status}
                  showSenderIdentity={isGroupConversation}
                  onReply={setReplyTo}
                  onForward={setForwardMessage}
                  onToggleReaction={toggleReaction}
                  onEdit={(target) => {
                    setMessageActionError(target.id, null);
                    setEditingMessage(target);
                  }}
                  onDelete={(target) => {
                    setConfirmationError(null);
                    setConfirmation({ kind: "delete-message", message: target });
                  }}
                  onRetry={handleRetry}
                  onReport={(target) => {
                    setConfirmationError(null);
                    setConfirmation({ kind: "report-message", message: target });
                  }}
                  onPreviewAttachment={setPreviewAttachmentId}
                  currentUserId={String(user?.id || "")}
                  onJumpToReply={(messageId) => void jumpToMessage(messageId)}
                  actionError={messageActionErrors[message.id] || null}
                  actionPending={Boolean(messageActionPending[message.id])}
                  warmMedia={index >= Math.max(0, messages.length - 6)}
                />
              </div>
            );
          })}
        </section>

        <footer className="ms-chat-composer-dock">
          {showJumpToLatest ? (
            <button
              type="button"
              className="ms-chat-jump-latest"
              onClick={scrollToLatest}
              aria-label="Jump to latest message"
              title="Jump to latest message"
            >
              <JumpToLatestIcon />
            </button>
          ) : null}
          <TypingIndicator names={Object.values(typingUsers).filter(Boolean)} />
          <MessageComposer
            draftKey={buildConversationDraftKey(String(user?.id || "anonymous"), conversationId)}
            legacyDraftKey={buildLegacyConversationDraftKey(conversationId)}
            uploadPolicy={composerUploadPolicy}
            onUpload={async (file, options) => {
              const upload = await uploadConversationAttachment(file, undefined, options);
              return {
                uploadId: upload.id,
                mediaKind: upload.mediaKind,
                width: upload.width,
                height: upload.height,
                rotation: upload.rotation,
                durationSeconds: upload.durationSeconds,
                thumbnailBlob: upload.encryptedPreview,
              };
            }}
            onDiscardUpload={(uploadId) => {
              delete encryptedAttachmentUploadsRef.current[uploadId];
            }}
            onSend={handleSaveMessage}
            replyTo={replyTo}
            onClearReply={() => setReplyTo(null)}
            editingMessage={editingMessage}
            onCancelEdit={() => setEditingMessage(null)}
            onTyping={sendTyping}
            disabled={!encryptionReadiness.canEncrypt}
            disabledReason={composerDisabledReason}
            onSendVoiceNote={async ({ file, previewUrl, fileName, mimeType, durationSeconds, clientTempId, waveform, waveformPromise }) => {
              let normalizedWaveform = waveform.map((value) => Math.max(0, Math.min(100, Math.round(value * 100))));
              const optimisticAttachment: MessageAttachment = {
                  id: `voice-${clientTempId}`,
                  original_name: fileName,
                  mime_type: mimeType,
                  media_kind: "audio",
                  size: file.size,
                  duration_seconds: durationSeconds,
                  file_url: previewUrl,
                  preview_url: previewUrl,
                  is_encrypted: false,
              };
              if (user?.id) {
                const optimistic = buildOptimisticMessage(
                  String(user.id),
                  user.username || "You",
                  "",
                  [optimisticAttachment.id],
                  null,
                  clientTempId,
                  [optimisticAttachment],
                  { type: "audio", isVoiceNote: true, durationSeconds, waveform: normalizedWaveform },
                );
                queryClient.setQueryData<InfiniteData<MessagePage>>(
                  ["messages", conversationId],
                  (current) => upsertMessagePages(current, optimistic),
                );
              }

              let sendStarted = false;
              try {
                const upload = await uploadConversationAttachment(file, {
                  original_name: fileName,
                  mime_type: mimeType,
                });
                const storedEnvelope = encryptedAttachmentUploadsRef.current[upload.id];
                if (!storedEnvelope) throw new Error("The secure voice note is not ready. Record it again.");
                const envelope = await rewrapAttachmentEncryptionForConversation({
                  userId: String(user?.id || ""),
                  conversationId,
                  envelope: storedEnvelope,
                  participantUserIds: conversationParticipantIds,
                });
                encryptedAttachmentUploadsRef.current[upload.id] = envelope;
                const analyzedWaveform = await waveformPromise?.catch(() => null);
                if (analyzedWaveform?.length) {
                  normalizedWaveform = analyzedWaveform.map((value) => Math.max(0, Math.min(100, Math.round(value * 100))));
                }
                sendStarted = true;
                await sendMutation.mutateAsync({
                  type: "audio",
                  attachment_ids: [upload.id],
                  attachment_encryption: [{ upload_id: upload.id, ...envelope }],
                  text: "",
                  is_voice_note: true,
                  duration_seconds: durationSeconds,
                  waveform: normalizedWaveform,
                  _optimistic_attachments: [{ ...optimisticAttachment, id: upload.id, encryption: envelope }],
                  client_temp_id: clientTempId,
                });
              } catch (error) {
                if (!sendStarted) {
                  queryClient.setQueryData<InfiniteData<MessagePage>>(
                    ["messages", conversationId],
                    (current) => removeMessagePages(current, (message) => message.id === `temp-${clientTempId}`),
                  );
                }
                throw error;
              }
            }}
          />
        </footer>
      </section>

      {showDetails ? (
        <>
          <button
            type="button"
            className={`ms-conversation-resizer ms-conversation-resizer--details ${isResizingDetails ? "is-dragging" : ""}`}
            onPointerDown={startDetailsResize}
            onKeyDown={(event) => {
              const step = event.shiftKey ? 32 : 16;
              if (event.key === "ArrowLeft") setDetailsWidth((current) => clampDetailsWidth(current + step, inboxWidth));
              else if (event.key === "ArrowRight") setDetailsWidth((current) => clampDetailsWidth(current - step, inboxWidth));
              else if (event.key === "Home") setDetailsWidth(DETAILS_MIN_WIDTH);
              else if (event.key === "End") setDetailsWidth(clampDetailsWidth(DETAILS_MAX_WIDTH, inboxWidth));
              else return;
              event.preventDefault();
            }}
            role="separator"
            aria-label="Resize details panel"
            aria-orientation="vertical"
            aria-valuemin={DETAILS_MIN_WIDTH}
            aria-valuemax={DETAILS_MAX_WIDTH}
            aria-valuenow={detailsWidth}
            aria-keyshortcuts="ArrowLeft ArrowRight Home End"
            title="Use left and right arrow keys to resize conversation details"
          />
          <button type="button" className="ms-conversation-details-backdrop" onClick={() => setShowDetails(false)} aria-label="Close conversation details" />
          <div className="ms-conversation-view__details">
            <ConversationDetailsPanel
              open
              conversation={conversationQuery.data}
              notifications={notificationsQuery.data}
              media={mediaQuery.data ?? []}
              allMedia={allMediaQuery.data ?? []}
              mediaKind={mediaKind}
              securityMaterial={e2eeMaterialQuery.data}
              securityReadiness={encryptionReadiness}
              onChangeMediaKind={setMediaKind}
              onClose={() => setShowDetails(false)}
              onStartVoiceCall={() => void startCall("voice")}
              onStartVideoCall={() => void startCall("video")}
              onToggleNotification={(patch) => { void chatApi.updateConversationNotifications(conversationId, patch).then(() => queryClient.invalidateQueries({ queryKey: ["conversation-notifications", conversationId] })); }}
              onSetMuteHours={(hours) => {
                const muted_until = hours ? new Date(Date.now() + hours * 60 * 60 * 1000).toISOString() : null;
                void chatApi.updateConversationNotifications(conversationId, { muted_until }).then(() => queryClient.invalidateQueries({ queryKey: ["conversation-notifications", conversationId] }));
              }}
              conversationStatePending={conversationStatePending}
              onToggleConversationState={(state) => {
                if (conversationStatePending) return;
                setConversationStateError(null);
                setConversationStatePending(state);
                const field = state === "pin" ? "is_pinned" : state === "archive" ? "is_archived" : "is_muted";
                const action = state === "pin"
                  ? chatApi.toggleConversationPin(conversationId)
                  : state === "archive"
                    ? chatApi.toggleConversationArchive(conversationId)
                    : chatApi.toggleConversationMute(conversationId);
                void action.then((payload) => {
                  const value = Boolean(payload[field]);
                  queryClient.setQueryData<Conversation>(["conversation", conversationId], (current) =>
                    patchConversationViewerState(current, conversationId, String(user?.id || ""), field, value),
                  );
                  queryClient.setQueryData<Conversation[]>(["conversations"], (current = []) =>
                    current.map((item) => patchConversationViewerState(item, conversationId, String(user?.id || ""), field, value) ?? item),
                  );
                }).catch((error) => {
                  setConversationStateError(getErrorMessage(error, "Could not update this conversation."));
                }).finally(() => {
                  setConversationStatePending(null);
                });
              }}
              onLeaveConversation={() => {
                setConfirmationError(null);
                setConfirmation({ kind: "leave-conversation" });
              }}
              onDeleteConversation={() => {
                setConfirmationError(null);
                setConfirmation({
                  kind: "delete-conversation",
                  title: conversationDisplayName(conversationQuery.data!, String(user?.id || ""), user),
                  isGroup: conversationQuery.data?.type === "group",
                });
              }}
              onBlockContact={(participantUserId) => {
                const participant = (conversationQuery.data?.participants ?? []).find((item) => String(item.user.id) === String(participantUserId));
                setConfirmationError(null);
                setConfirmation({
                  kind: "block-contact",
                  userId: String(participantUserId),
                  displayName: participant?.user.display_name || participant?.user.username || "this contact",
                });
              }}
            />
          </div>
        </>
      ) : null}

      <MediaPreviewModal
        attachment={previewAttachment}
        currentUserId={String(user?.id || "")}
        onClose={() => setPreviewAttachmentId(null)}
        onPrevious={previewAttachmentIndex > 0 ? () => setPreviewAttachmentId(previewAttachments[previewAttachmentIndex - 1]?.id || null) : undefined}
        onNext={previewAttachmentIndex >= 0 && previewAttachmentIndex < previewAttachments.length - 1 ? () => setPreviewAttachmentId(previewAttachments[previewAttachmentIndex + 1]?.id || null) : undefined}
        onReply={previewMessage ? () => {
          setReplyTo(previewMessage);
          setPreviewAttachmentId(null);
        } : undefined}
        onForward={previewMessage ? () => {
          setForwardMessage(previewMessage);
          setPreviewAttachmentId(null);
        } : undefined}
      />

      {forwardMessage ? <ForwardMessageModal message={forwardMessage} conversations={conversationsQuery.data ?? []} onClose={() => setForwardMessage(null)} onForward={async (targetConversationId: string) => {
        await chatApi.forwardMessage(forwardMessage.id, targetConversationId);
        setForwardMessage(null);
      }} /> : null}

      <ConfirmDialog
        open={Boolean(confirmation && confirmationContent)}
        title={confirmationContent?.title || "Confirm action"}
        description={confirmationContent?.description || "Please confirm this action."}
        confirmLabel={confirmationContent?.confirmLabel || "Confirm"}
        tone="danger"
        pending={confirmationPending}
        error={confirmationError}
        onConfirm={() => void runConfirmedAction()}
        onClose={() => {
          if (confirmationPending) return;
          setConfirmation(null);
          setConfirmationError(null);
        }}
      />
    </div>
  );
}
