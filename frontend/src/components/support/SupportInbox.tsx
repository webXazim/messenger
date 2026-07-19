import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { supportApi } from "../../api/support";
import { parseApiError } from "../../lib/apiErrors";
import type {
  SupportBootstrap,
  SupportConversation,
  SupportConversationFilters,
  SupportConversationMessagesResponse,
  SupportConversationUpdateInput,
  SupportAttachment,
  SupportMessage,
  SupportCall,
} from "../../types/support";
import type { Message } from "../../types/chat";
import { useSupportSocketStatus } from "../../hooks/useSupportRealtime";
import { SupportConversationTools } from "./SupportConversationTools";
import { MessageBubble as MessengerMessageBubble } from "../MessageBubble";
import { MessageComposer } from "../MessageComposer";
import { ChatHeader, type ChatHeaderNotice } from "../conversation/ChatHeader";
import type { VoiceNotePayload } from "../VoiceNoteRecorder";
import { SupportGuestCall } from "./SupportGuestCall";
import { TypingIndicator } from "../TypingIndicator";
import { supportSocket } from "../../lib/supportSocket";
import { createSerializedTaskQueue } from "../../lib/serializedTaskQueue";
import { TYPING_MESSAGE_TRANSITION_MS, typingRemovalDelay } from "../../lib/typingPresence";

const QUEUES: Array<{
  value: NonNullable<SupportConversationFilters["queue"]>;
  label: string;
}> = [
  { value: "open", label: "Open" },
  { value: "mine", label: "Mine" },
  { value: "unassigned", label: "Unassigned" },
  { value: "overdue", label: "Overdue" },
  { value: "follow_up", label: "Follow-ups" },
  { value: "resolved", label: "Resolved" },
  { value: "closed", label: "Closed" },
];

function visitorName(conversation: SupportConversation) {
  return (
    conversation.visitor.name || conversation.visitor.email || "Website visitor"
  );
}

function buildOptimisticSupportMessage(
  bootstrap: SupportBootstrap,
  payload: Record<string, unknown>,
): SupportMessage {
  const clientTempId = String(payload.client_temp_id || Date.now());
  const now = new Date().toISOString();
  const rawAttachments = Array.isArray(payload._optimistic_attachments)
    ? payload._optimistic_attachments
    : [];
  const attachments: SupportAttachment[] = rawAttachments.map((raw) => {
    const attachment = raw as Record<string, unknown>;
    return {
      id: String(attachment.id || ""),
      media_kind: String(attachment.media_kind || "file") as SupportAttachment["media_kind"],
      original_name: String(attachment.original_name || "Attachment"),
      mime_type: String(attachment.mime_type || "application/octet-stream"),
      size: Number(attachment.size || 0),
      width: attachment.width == null ? null : Number(attachment.width),
      height: attachment.height == null ? null : Number(attachment.height),
      rotation: attachment.rotation == null ? null : Number(attachment.rotation),
      duration_seconds: attachment.duration_seconds == null ? null : Number(attachment.duration_seconds),
      scan_status: "clean",
      can_preview_inline: true,
      download_url: String(attachment.file_url || attachment.preview_url || ""),
      preview_url: attachment.preview_url ? String(attachment.preview_url) : null,
      thumbnail_url: attachment.thumbnail_url ? String(attachment.thumbnail_url) : null,
    };
  });
  const text = String(payload.text || "").trim();
  const firstMediaKind = attachments[0]?.media_kind;
  return {
    id: `temp-${clientTempId}`,
    client_temp_id: clientTempId,
    type: firstMediaKind || "text",
    text,
    created_at: now,
    updated_at: now,
    delivery_status: "pending",
    receipt_status: "pending",
    delivered_at: null,
    read_at: null,
    sender: {
      kind: bootstrap.role === "agent" ? "agent" : "owner",
      id: null,
      display_name: "You",
    },
    is_own: true,
    voice_note: Boolean(payload.voice_note),
    attachments,
    preview_text: text || attachments[0]?.original_name || "Attachment",
  };
}

function mergeSupportMessages(
  currentValue: unknown,
  incomingValue: unknown,
) {
  const current = currentValue as SupportConversationMessagesResponse | undefined;
  const incoming = incomingValue as SupportConversationMessagesResponse;
  if (!current) return incoming;
  const committedTempIds = new Set(
    incoming.messages
      .map((message) => message.client_temp_id)
      .filter((clientTempId): clientTempId is string => Boolean(clientTempId)),
  );
  const localMessages = current.messages.filter(
    (message) =>
      message.id.startsWith("temp-")
      && !committedTempIds.has(message.client_temp_id || message.id.slice(5)),
  );
  if (!localMessages.length) return incoming;
  return {
    ...incoming,
    messages: [...incoming.messages, ...localMessages],
  };
}

function formatCompactTime(value?: string | null) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const today = new Date();
  if (date.toDateString() === today.toDateString()) {
    return new Intl.DateTimeFormat(undefined, {
      hour: "numeric",
      minute: "2-digit",
    }).format(date);
  }
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
  }).format(date);
}

function formatServiceDeadline(value?: string | null) {
  if (!value) return "No active target";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "No active target";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function toLocalDateTimeInput(value?: string | null) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return shifted.toISOString().slice(0, 16);
}

function serviceTargetLabel(value?: string | null) {
  return value ? value.replace(/_/g, " ") : "Service target";
}

function ConversationRow({
  conversation,
  active,
  onOpen,
}: {
  conversation: SupportConversation;
  active: boolean;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      className={`ms-support-conversation-row${active ? " is-active" : ""}`}
      onClick={onOpen}
    >
      <span className="ms-support-conversation-row__avatar" aria-hidden="true">
        {visitorName(conversation).slice(0, 1).toUpperCase()}
      </span>
      <span className="ms-support-conversation-row__body">
        <span className="ms-support-conversation-row__topline">
          <strong>{visitorName(conversation)}</strong>
          <time>
            {formatCompactTime(
              conversation.last_message?.created_at || conversation.created_at,
            )}
          </time>
        </span>
        <span className="ms-support-conversation-row__site">
          {conversation.website.name}
        </span>
        <span className="ms-support-conversation-row__preview">
          {conversation.last_message?.sender.kind !== "visitor" &&
          conversation.last_message
            ? "Team: "
            : ""}
          {conversation.last_message?.preview_text ||
            conversation.last_message?.text ||
            "New support conversation"}
        </span>
        {conversation.tags?.length ? (
          <span className="ms-support-conversation-row__tags">
            {conversation.tags.slice(0, 3).map((tag) => (
              <span className="ms-support-tag-dot" style={{ background: tag.color }} title={tag.name} key={tag.id} />
            ))}
          </span>
        ) : null}
      </span>
      {conversation.service?.state === "overdue" || conversation.service?.follow_up_due ? (
        <span className="ms-support-service-badge is-overdue" title="Service action overdue">!</span>
      ) : conversation.service?.state === "due_soon" ? (
        <span className="ms-support-service-badge is-due-soon" title="Service target due soon">•</span>
      ) : null}
      {conversation.unread_count > 0 ? (
        <span className="ms-support-unread-badge">
          {Math.min(99, conversation.unread_count)}
        </span>
      ) : null}
    </button>
  );
}

function SupportCSATPanel({ conversation, disabled }: { conversation: SupportConversation; disabled: boolean }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const settingsQuery = useQuery({
    queryKey: ["support-feedback-settings"],
    queryFn: ({ signal }) => supportApi.getFeedbackSettings(signal),
    staleTime: 60_000,
  });
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["support-conversations"] }),
      queryClient.invalidateQueries({ queryKey: ["support-conversation-messages", conversation.id] }),
    ]);
  };
  const requestMutation = useMutation({
    mutationFn: () => supportApi.requestConversationCSAT(conversation.id),
    onMutate: () => setError(null),
    onSuccess: refresh,
    onError: (mutationError) => setError(parseApiError(mutationError, "Feedback could not be requested.").message),
  });
  const dismissMutation = useMutation({
    mutationFn: () => supportApi.dismissConversationCSAT(conversation.id),
    onMutate: () => setError(null),
    onSuccess: refresh,
    onError: (mutationError) => setError(parseApiError(mutationError, "Feedback request could not be dismissed.").message),
  });

  if (!["resolved", "closed"].includes(conversation.status)) return null;
  const survey = conversation.csat;
  const enabled = settingsQuery.data?.csat_enabled !== false;
  return (
    <section className="ms-support-csat-panel">
      <div className="ms-support-detail-section__heading"><strong>Customer satisfaction</strong><span>Private Support metric</span></div>
      {survey?.status === "submitted" ? (
        <div className="ms-support-csat-result">
          <strong aria-label={`${survey.rating || 0} out of 5`}>{"★".repeat(survey.rating || 0)}{"☆".repeat(5 - (survey.rating || 0))}</strong>
          <span>{survey.rating} out of 5</span>
          {survey.comment ? <p>{survey.comment}</p> : null}
        </div>
      ) : survey?.status === "pending" && survey.available ? (
        <div className="ms-support-csat-pending">
          <span>Feedback requested</span>
          <small>Available until {new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(survey.expires_at))}</small>
          <button type="button" className="ms-button ms-button--ghost ms-button--compact" disabled={disabled || dismissMutation.isPending} onClick={() => dismissMutation.mutate()}>Cancel request</button>
        </div>
      ) : (
        <div className="ms-support-csat-request">
          <p>{enabled ? "Ask this visitor for a simple 1–5 rating." : "Customer feedback is disabled in Support settings."}</p>
          {enabled ? <button type="button" className="ms-button ms-button--ghost ms-button--compact" disabled={disabled || requestMutation.isPending} onClick={() => requestMutation.mutate()}>{requestMutation.isPending ? "Requesting…" : "Request feedback"}</button> : null}
        </div>
      )}
      {error ? <div className="ms-support-error">{error}</div> : null}
    </section>
  );
}

function toMessengerMessage(message: SupportMessage): Message {
  const voiceAttachment = message.attachments.find(
    (attachment) => attachment.media_kind === "audio",
  );
  return {
    id: message.id,
    type: message.type,
    text: message.text,
    created_at: message.created_at,
    updated_at: message.updated_at,
    delivery_status: message.receipt_status || message.delivery_status,
    sender: {
      id: message.sender.id || `${message.sender.kind}-${message.id}`,
      username: message.sender.username || message.sender.kind,
      display_name: message.sender.display_name,
      avatar: message.sender.avatar,
    },
    attachments: message.attachments.map((attachment) => ({
      ...attachment,
      file_url: attachment.download_url,
      preview_url: attachment.preview_url || undefined,
      thumbnail_url: attachment.thumbnail_url,
    })),
    voice_note: message.voice_note
      ? {
          is_voice_note: true,
          duration_seconds: voiceAttachment?.duration_seconds,
        }
      : null,
    can_edit: false,
    is_deleted: false,
    is_encrypted: false,
  };
}

function SupportMessageBubble({
  message,
  groupPosition,
}: {
  message: SupportMessage;
  groupPosition: "single" | "start" | "middle" | "end";
}) {
  const messengerMessage = toMessengerMessage(message);
  return (
    <MessengerMessageBubble
      message={messengerMessage}
      own={message.sender.kind !== "visitor"}
      groupPosition={groupPosition}
      showSenderIdentity={false}
      actionsEnabled={false}
      onReply={() => undefined}
      onForward={() => undefined}
      onToggleReaction={() => undefined}
      onEdit={() => undefined}
      onDelete={() => undefined}
    />
  );
}

function ConversationDetails({
  conversation,
  bootstrap,
  disabled,
  onUpdate,
  defaultFollowUpMinutes,
}: {
  conversation: SupportConversation;
  bootstrap: SupportBootstrap;
  disabled: boolean;
  onUpdate: (payload: SupportConversationUpdateInput) => void;
  defaultFollowUpMinutes: number;
}) {
  const canAssign =
    bootstrap.role === "owner" ||
    Boolean(bootstrap.agents[0]?.can_assign_conversations);
  const websiteAgents = bootstrap.agents.filter((agent) =>
    agent.assigned_website_ids.includes(conversation.website.id),
  );
  const defaultFollowUpValue = () =>
    toLocalDateTimeInput(
      conversation.service?.follow_up_at ||
        new Date(Date.now() + defaultFollowUpMinutes * 60000).toISOString(),
    );
  const [followUpAt, setFollowUpAt] = useState(defaultFollowUpValue);
  const [followUpNote, setFollowUpNote] = useState(
    conversation.service?.follow_up_note || "",
  );
  useEffect(() => {
    setFollowUpAt(defaultFollowUpValue());
    setFollowUpNote(conversation.service?.follow_up_note || "");
  }, [conversation.id, conversation.service?.follow_up_at, conversation.service?.follow_up_note, defaultFollowUpMinutes]);
  const scheduleFollowUp = () => {
    if (!followUpAt) return;
    const date = new Date(followUpAt);
    if (Number.isNaN(date.getTime())) return;
    onUpdate({ follow_up_at: date.toISOString(), follow_up_note: followUpNote.trim() });
  };
  return (
    <div className="ms-support-details-content">
      <div className="ms-support-detail-person">
        <span
          className="ms-support-conversation-row__avatar"
          aria-hidden="true"
        >
          {visitorName(conversation).slice(0, 1).toUpperCase()}
        </span>
        <div>
          <strong>{visitorName(conversation)}</strong>
          <span>{conversation.visitor.email || "Email not provided"}</span>
        </div>
      </div>
      <dl className="ms-support-detail-list">
        <div>
          <dt>Website</dt>
          <dd>
            {conversation.website.name}
            <small>{conversation.website.domain}</small>
          </dd>
        </div>
        <div>
          <dt>Current page</dt>
          <dd>{conversation.visitor.current_page_url || "Not available"}</dd>
        </div>
        <div>
          <dt>Last seen</dt>
          <dd>{formatCompactTime(conversation.visitor.last_seen_at)}</dd>
        </div>
      </dl>
      <section className={`ms-support-service-summary is-${conversation.service?.state || "none"}`}>
        <div>
          <span>{serviceTargetLabel(conversation.service?.active_target)}</span>
          <strong>{formatServiceDeadline(conversation.service?.active_due_at)}</strong>
        </div>
        <span>{conversation.service?.state === "overdue" ? "Overdue" : conversation.service?.state === "due_soon" ? "Due soon" : conversation.service?.state === "on_track" ? "On track" : "No active timer"}</span>
      </section>
      <label className="ms-support-control-field">
        <span>Status</span>
        <select
          value={conversation.status}
          disabled={disabled}
          onChange={(event) =>
            onUpdate({
              status: event.target.value as SupportConversation["status"],
            })
          }
        >
          <option value="new">New</option>
          <option value="open">Open</option>
          <option value="waiting_customer">Waiting for customer</option>
          <option value="waiting_team">Waiting for team</option>
          <option value="resolved">Resolved</option>
          <option value="closed">Closed</option>
        </select>
      </label>
      <label className="ms-support-control-field">
        <span>Priority</span>
        <select
          value={conversation.priority}
          disabled={disabled}
          onChange={(event) =>
            onUpdate({
              priority: event.target.value as SupportConversation["priority"],
            })
          }
        >
          <option value="low">Low</option>
          <option value="normal">Normal</option>
          <option value="high">High</option>
          <option value="urgent">Urgent</option>
        </select>
      </label>
      {canAssign ? (
        <label className="ms-support-control-field">
          <span>Assigned agent</span>
          <select
            value={conversation.assigned_agent?.id || ""}
            disabled={disabled}
            onChange={(event) =>
              onUpdate({ assigned_agent_id: event.target.value || null })
            }
          >
            <option value="">Unassigned</option>
            {websiteAgents.map((agent) => (
              <option value={agent.id} key={agent.id}>
                {agent.user.display_name}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <section className="ms-support-follow-up-editor">
        <div className="ms-support-detail-section__heading"><strong>Follow-up</strong><span>Private team reminder</span></div>
        <label className="ms-support-control-field">
          <span>Remind at</span>
          <input type="datetime-local" value={followUpAt} disabled={disabled} onChange={(event) => setFollowUpAt(event.target.value)} />
        </label>
        <label className="ms-support-control-field">
          <span>Reminder note</span>
          <input value={followUpNote} maxLength={255} disabled={disabled} onChange={(event) => setFollowUpNote(event.target.value)} placeholder="Optional context" />
        </label>
        <div className="ms-support-follow-up-actions">
          {conversation.service?.follow_up_at && !conversation.service?.follow_up_completed_at ? (
            <button type="button" className="ms-button ms-button--ghost ms-button--compact" disabled={disabled} onClick={() => onUpdate({ follow_up_at: null, follow_up_note: "" })}>Clear</button>
          ) : null}
          <button type="button" className="ms-button ms-button--primary ms-button--compact" disabled={disabled || !followUpAt} onClick={scheduleFollowUp}>Schedule</button>
        </div>
        {conversation.service?.follow_up_due ? <span className="ms-support-follow-up-overdue">Follow-up is due now.</span> : null}
      </section>
      <SupportCSATPanel conversation={conversation} disabled={disabled} />
      <SupportConversationTools conversation={conversation} />
    </div>
  );
}

export function SupportInbox({ bootstrap }: { bootstrap: SupportBootstrap }) {
  const queryClient = useQueryClient();
  const socketStatus = useSupportSocketStatus();
  const [searchParams, setSearchParams] = useSearchParams();
  const [queue, setQueue] =
    useState<NonNullable<SupportConversationFilters["queue"]>>("open");
  const [website, setWebsite] = useState("");
  const [search, setSearch] = useState("");
  const [priority, setPriority] = useState("");
  const [tag, setTag] = useState("");
  const [savedViewName, setSavedViewName] = useState("");
  const [selectedSavedViewId, setSelectedSavedViewId] = useState("");
  const [selectedId, setSelectedId] = useState(
    () => searchParams.get("conversation") || "",
  );
  const [error, setError] = useState<string | null>(null);
  const [activeCall, setActiveCall] = useState<SupportCall | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(
    () => typeof window === "undefined" || window.innerWidth > 1180,
  );
  const [composerInsertion, setComposerInsertion] = useState<{
    id: string;
    text: string;
  } | null>(null);
  const [typingVisitors, setTypingVisitors] = useState<Record<string, string>>({});
  const timelineRef = useRef<HTMLDivElement | null>(null);
  const typingStopTimerRef = useRef<number | null>(null);
  const typingVisitorTimerRef = useRef<number | null>(null);
  const typingVisitorShownAtRef = useRef(0);
  const teamTypingActiveRef = useRef(false);
  const sendQueueRef = useRef(createSerializedTaskQueue());

  const filters = useMemo(
    () => ({
      queue,
      website: website || undefined,
      search: search.trim() || undefined,
      priority: (priority || undefined) as SupportConversationFilters["priority"],
      tag: tag || undefined,
    }),
    [queue, website, search, priority, tag],
  );
  const listQuery = useQuery({
    queryKey: ["support-conversations", filters],
    queryFn: ({ signal }) => supportApi.listConversations(filters, signal),
    refetchInterval: socketStatus === "open" ? false : 5000,
    staleTime: socketStatus === "open" ? 10_000 : 2000,
  });
  const tagsQuery = useQuery({
    queryKey: ["support-tags"],
    queryFn: ({ signal }) => supportApi.listTags(signal),
  });
  const savedViewsQuery = useQuery({
    queryKey: ["support-saved-views"],
    queryFn: ({ signal }) => supportApi.listSavedViews(signal),
  });
  const alertsQuery = useQuery({
    queryKey: ["support-service-alerts"],
    queryFn: ({ signal }) => supportApi.listServiceAlerts("unread", signal),
    refetchInterval: socketStatus === "open" ? false : 10000,
    staleTime: socketStatus === "open" ? 10000 : 3000,
  });
  const serviceSettingsQuery = useQuery({
    queryKey: ["support-service-settings"],
    queryFn: ({ signal }) => supportApi.getServiceSettings(signal),
    staleTime: 60_000,
  });
  const callSettingsQuery = useQuery({
    queryKey: ["support-call-settings"],
    queryFn: ({ signal }) => supportApi.getCallSettings(signal),
    staleTime: 60_000,
  });
  const activeCallQuery = useQuery({
    queryKey: ["support-active-call"],
    queryFn: ({ signal }) => supportApi.getActiveCall(signal),
    refetchInterval: activeCall ? false : 5000,
    staleTime: 1500,
  });
  const selectedFromList =
    listQuery.data?.results.find((item) => item.id === selectedId) || null;
  const messagesQuery = useQuery<SupportConversationMessagesResponse>({
    queryKey: ["support-conversation-messages", selectedId],
    queryFn: ({ signal }) =>
      supportApi.getConversationMessages(selectedId, signal),
    enabled: Boolean(selectedId),
    refetchInterval: selectedId && socketStatus !== "open" ? 4000 : false,
    structuralSharing: mergeSupportMessages,
  });
  const selectedConversation =
    messagesQuery.data?.conversation || selectedFromList;
  const cannedRepliesQuery = useQuery({
    queryKey: ["support-canned-replies", selectedConversation?.website.id || ""],
    queryFn: ({ signal }) => supportApi.listCannedReplies(selectedConversation?.website.id, signal),
    enabled: Boolean(selectedConversation?.website.id),
  });
  const knowledgeArticlesQuery = useQuery({
    queryKey: ["support-knowledge-articles", "reply", selectedConversation?.website.id || ""],
    queryFn: ({ signal }) => supportApi.listKnowledgeArticles({ website: selectedConversation?.website.id, status: "published" }, signal),
    enabled: Boolean(selectedConversation?.website.id),
    staleTime: 60_000,
  });

  const openConversation = (conversationId: string) => {
    setSelectedId(conversationId);
    if (conversationId && typeof window !== "undefined" && window.innerWidth <= 1180) {
      setDetailsOpen(false);
    }
    const next = new URLSearchParams(searchParams);
    if (conversationId) next.set("conversation", conversationId);
    else next.delete("conversation");
    setSearchParams(next, { replace: true });
  };

  useEffect(() => {
    if (
      selectedId ||
      !listQuery.data?.results.length ||
      typeof window === "undefined" ||
      window.innerWidth <= 760
    )
      return;
    openConversation(listQuery.data.results[0].id);
  }, [listQuery.data?.results, selectedId]);

  useEffect(() => {
    setError(null);
  }, [selectedId]);

  useEffect(() => {
    const recovered = activeCallQuery.data?.call || null;
    if (recovered && !["declined", "missed", "ended", "failed"].includes(recovered.status)) {
      setActiveCall((current) => current?.id === recovered.id ? current : recovered);
    }
  }, [activeCallQuery.data?.call]);

  useEffect(() => {
    if (!messagesQuery.data?.messages.length) return;
    const frame = window.requestAnimationFrame(() => {
      timelineRef.current?.scrollTo({
        top: timelineRef.current.scrollHeight,
        behavior: "smooth",
      });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [messagesQuery.data?.messages.length, selectedId]);

  useEffect(() => {
    if (!selectedId || !messagesQuery.data) return;
    void queryClient.invalidateQueries({
      queryKey: ["support-unread-summary"],
    });
  }, [messagesQuery.data, queryClient, selectedId]);

  useEffect(() => {
    return supportSocket.subscribe((payload) => {
      const conversationId = String(payload.data?.conversation_id || "");
      if (
        payload.event === "support.call.ringing" &&
        String(payload.data?.initiator_kind || "") === "visitor"
      ) {
        const incomingCall = payload.data as unknown as SupportCall;
        setActiveCall(incomingCall);
        queryClient.setQueryData(["support-active-call"], { call: incomingCall });
        if (conversationId && conversationId !== selectedId) openConversation(conversationId);
        return;
      }
      if (conversationId && conversationId !== selectedId) return;

      if (
        payload.event === "support.typing.started" ||
        payload.event === "support.typing.stopped"
      ) {
        const sender = payload.data?.sender && typeof payload.data.sender === "object"
          ? payload.data.sender as Record<string, unknown>
          : {};
        if (String(sender.kind || "") !== "visitor") return;
        const visitorId = String(sender.id || selectedConversation?.visitor.id || "visitor");
        if (typingVisitorTimerRef.current) {
          window.clearTimeout(typingVisitorTimerRef.current);
          typingVisitorTimerRef.current = null;
        }
        if (payload.event === "support.typing.stopped") {
          const delay = typingRemovalDelay(
            typingVisitorShownAtRef.current,
            TYPING_MESSAGE_TRANSITION_MS,
          );
          typingVisitorTimerRef.current = window.setTimeout(() => {
            setTypingVisitors({});
            typingVisitorTimerRef.current = null;
          }, delay);
          return;
        }
        typingVisitorShownAtRef.current = Date.now();
        setTypingVisitors({
          [visitorId]: String(sender.display_name || "Website visitor"),
        });
        typingVisitorTimerRef.current = window.setTimeout(() => {
          setTypingVisitors({});
          typingVisitorTimerRef.current = null;
        }, 7500);
        return;
      }

      if (payload.event === "support.visitor.presence") {
        queryClient.setQueryData<SupportConversationMessagesResponse>(
          ["support-conversation-messages", selectedId],
          (current) => current
            ? {
                ...current,
                conversation: {
                  ...current.conversation,
                  visitor: {
                    ...current.conversation.visitor,
                    is_online: Boolean(payload.data?.is_online),
                    last_seen_at: String(
                      payload.data?.last_seen_at ||
                      current.conversation.visitor.last_seen_at,
                    ),
                    current_page_url: String(
                      payload.data?.current_page_url ||
                      current.conversation.visitor.current_page_url,
                    ),
                    referrer: String(
                      payload.data?.referrer ||
                      current.conversation.visitor.referrer,
                    ),
                  },
                },
              }
            : current,
        );
        return;
      }

      if (
        payload.event !== "support.message.delivered" &&
        payload.event !== "support.message.read"
      ) return;
      const pointerId = String(payload.data?.message_id || "");
      const actorKind = String(payload.data?.actor_kind || "");
      const nextStatus = payload.event.endsWith(".read") ? "read" : "delivered";
      queryClient.setQueryData<SupportConversationMessagesResponse>(
        ["support-conversation-messages", selectedId],
        (current) => {
          if (!current || !pointerId) return current;
          const pointerIndex = current.messages.findIndex((item) => item.id === pointerId);
          if (pointerIndex < 0) return current;
          return {
            ...current,
            messages: current.messages.map((item, index) => {
              const addressed =
                actorKind === "visitor"
                  ? item.sender.kind !== "visitor"
                  : item.sender.kind === "visitor";
              if (!addressed || index > pointerIndex) return item;
              return {
                ...item,
                receipt_status: nextStatus,
                delivered_at: item.delivered_at || String(payload.data?.occurred_at || ""),
                read_at: nextStatus === "read"
                  ? String(payload.data?.occurred_at || "")
                  : item.read_at,
              };
            }),
          };
        },
      );
    });
  }, [queryClient, selectedConversation?.visitor.id, selectedId]);

  useEffect(() => {
    const messages = messagesQuery.data?.messages || [];
    const latestVisitorMessage = [...messages]
      .reverse()
      .find((message) => message.sender.kind === "visitor");
    if (!selectedId || !latestVisitorMessage) return;
    void supportApi.markConversationDelivered(selectedId, latestVisitorMessage.id);
    if (document.visibilityState === "visible") {
      void supportApi.markConversationRead(selectedId, latestVisitorMessage.id);
    }
  }, [messagesQuery.data?.messages, selectedId, socketStatus]);

  useEffect(() => () => {
    if (typingStopTimerRef.current) window.clearTimeout(typingStopTimerRef.current);
    if (typingVisitorTimerRef.current) window.clearTimeout(typingVisitorTimerRef.current);
    teamTypingActiveRef.current = false;
    if (selectedId && supportSocket.isOpen()) {
      supportSocket.send({
        event: "support.typing.stop",
        data: {
          conversation_id: selectedId,
          website_id: selectedConversation?.website.id || "",
          visitor_id: selectedConversation?.visitor.id || "",
        },
      });
    }
  }, [selectedId]);

  const refreshConversation = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["support-conversations"] }),
      queryClient.invalidateQueries({ queryKey: ["support-service-alerts"] }),
      queryClient.invalidateQueries({ queryKey: ["support-unread-summary"] }),
      selectedId
        ? queryClient.invalidateQueries({
            queryKey: ["support-conversation-messages", selectedId],
          })
        : Promise.resolve(),
    ]);
  };

  const sendMutation = useMutation({
    mutationFn: (payload: {
      text?: string;
      attachment_ids?: string[];
      voice_note?: boolean;
      clientTempId?: string;
      conversationId: string;
    }) => {
      const { clientTempId, conversationId, ...messagePayload } = payload;
      return supportApi.sendConversationMessage(conversationId, {
        ...messagePayload,
        client_temp_id: clientTempId,
      });
    },
    onMutate: () => setError(null),
    onSuccess: (message, variables) => {
      queryClient.setQueryData<SupportConversationMessagesResponse>(
        ["support-conversation-messages", variables.conversationId],
        (current) => {
          if (!current) return current;
          const temporaryId = `temp-${variables.clientTempId}`;
          const temporaryIndex = current.messages.findIndex((item) => item.id === temporaryId);
          const messages = [...current.messages];
          if (temporaryIndex >= 0) messages.splice(temporaryIndex, 1, message);
          else if (!messages.some((item) => item.id === message.id)) messages.push(message);
          return {
              ...current,
              messages,
              conversation: {
                ...current.conversation,
                last_message: message,
                updated_at: message.created_at,
              },
            };
        },
      );
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["support-conversations"] }),
        queryClient.invalidateQueries({ queryKey: ["support-unread-summary"] }),
        queryClient.invalidateQueries({ queryKey: ["support-service-alerts"] }),
      ]);
    },
    onError: (mutationError, variables) => {
      queryClient.setQueryData<SupportConversationMessagesResponse>(
        ["support-conversation-messages", variables.conversationId],
        (current) => current
          ? {
              ...current,
              messages: current.messages.map((item) =>
                item.id === `temp-${variables.clientTempId}`
                  ? { ...item, delivery_status: "failed", receipt_status: "failed" }
                  : item
              ),
            }
          : current,
      );
      setError(
        parseApiError(mutationError, "The message could not be sent.").message,
      );
    },
  });
  const updateMutation = useMutation({
    mutationFn: (payload: SupportConversationUpdateInput) =>
      supportApi.updateConversation(selectedId, payload),
    onMutate: () => setError(null),
    onSuccess: refreshConversation,
    onError: (mutationError) =>
      setError(
        parseApiError(mutationError, "The conversation could not be updated.")
          .message,
      ),
  });
  const startCallMutation = useMutation({
    mutationFn: (callType: "voice" | "video") => supportApi.startConversationCall(selectedId, callType),
    onMutate: () => setError(null),
    onSuccess: (payload) => {
      setActiveCall(payload);
      queryClient.setQueryData(["support-active-call"], { call: payload });
    },
    onError: (mutationError) => setError(parseApiError(mutationError, "The support call could not be started.").message),
  });

  const claimMutation = useMutation({
    mutationFn: () => supportApi.claimConversation(selectedId),
    onMutate: () => setError(null),
    onSuccess: refreshConversation,
    onError: (mutationError) =>
      setError(
        parseApiError(mutationError, "The conversation could not be taken.")
          .message,
      ),
  });
  const saveViewMutation = useMutation({
    mutationFn: () => supportApi.createSavedView({
      name: savedViewName.trim(),
      website_id: website || null,
      queue,
      priority,
      tag_id: tag || null,
      search: search.trim(),
    }),
    onMutate: () => setError(null),
    onSuccess: async () => {
      setSavedViewName("");
      await queryClient.invalidateQueries({ queryKey: ["support-saved-views"] });
    },
    onError: (mutationError) => setError(parseApiError(mutationError, "The inbox view could not be saved.").message),
  });
  const removeViewMutation = useMutation({
    mutationFn: (viewId: string) => supportApi.removeSavedView(viewId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["support-saved-views"] }),
    onError: (mutationError) => setError(parseApiError(mutationError, "The saved view could not be removed.").message),
  });
  const readAlertMutation = useMutation({
    mutationFn: (alertId: string) => supportApi.markServiceAlertRead(alertId),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["support-service-alerts"] }),
        queryClient.invalidateQueries({ queryKey: ["support-unread-summary"] }),
      ]);
    },
  });
  const readAllAlertsMutation = useMutation({
    mutationFn: () => supportApi.markAllServiceAlertsRead(),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["support-service-alerts"] }),
        queryClient.invalidateQueries({ queryKey: ["support-unread-summary"] }),
      ]);
    },
  });

  const openServiceAlert = (alertId: string, conversationId: string) => {
    readAlertMutation.mutate(alertId);
    openConversation(conversationId);
  };

  const applySavedView = (viewId: string) => {
    const view = savedViewsQuery.data?.find((item) => item.id === viewId);
    if (!view) return;
    setWebsite(view.website_id || "");
    setQueue((view.queue || "open") as NonNullable<SupportConversationFilters["queue"]>);
    setPriority(view.priority || "");
    setTag(view.tag_id || "");
    setSearch(view.search || "");
    openConversation("");
  };

  const sendVoiceNote = async (payload: VoiceNotePayload) => {
    if (!selectedId) throw new Error("Select a conversation first.");
    setError(null);
    const optimisticMessage = buildOptimisticSupportMessage(bootstrap, {
      client_temp_id: payload.clientTempId,
      voice_note: true,
      _optimistic_attachments: [{
        id: `voice-${payload.clientTempId}`,
        original_name: payload.fileName,
        mime_type: payload.mimeType,
        media_kind: "audio",
        size: payload.file.size,
        duration_seconds: payload.durationSeconds,
        file_url: payload.previewUrl,
        preview_url: payload.previewUrl,
      }],
    });
    queryClient.setQueryData<SupportConversationMessagesResponse>(
      ["support-conversation-messages", selectedId],
      (current) => current
        ? { ...current, messages: [...current.messages, optimisticMessage] }
        : current,
    );
    try {
      const upload = await supportApi.uploadConversationFile(
        selectedId,
        payload.file,
        {
          durationSeconds: payload.durationSeconds,
          waveform: payload.waveform,
        },
      );
      await sendQueueRef.current.enqueue(() => sendMutation.mutateAsync({
        attachment_ids: [upload.id],
        voice_note: true,
        clientTempId: payload.clientTempId,
        conversationId: selectedId,
      }));
    } catch (voiceError) {
      queryClient.setQueryData<SupportConversationMessagesResponse>(
        ["support-conversation-messages", selectedId],
        (current) => current
          ? {
              ...current,
              messages: current.messages.filter(
                (item) => item.id !== `temp-${payload.clientTempId}`,
              ),
            }
          : current,
      );
      const message = parseApiError(
        voiceError,
        "The voice message could not be sent.",
      ).message;
      setError(message);
      throw new Error(message);
    }
  };

  const stopTeamTyping = () => {
    if (typingStopTimerRef.current) {
      window.clearTimeout(typingStopTimerRef.current);
      typingStopTimerRef.current = null;
    }
    if (teamTypingActiveRef.current && selectedId && supportSocket.isOpen()) {
      supportSocket.send({
        event: "support.typing.stop",
        data: {
          conversation_id: selectedId,
          website_id: selectedConversation?.website.id || "",
          visitor_id: selectedConversation?.visitor.id || "",
        },
      });
    }
    teamTypingActiveRef.current = false;
  };

  const sendTyping = () => {
    if (!selectedId || !supportSocket.isOpen()) return;
    if (!teamTypingActiveRef.current) {
      teamTypingActiveRef.current = true;
      supportSocket.send({
        event: "support.typing.start",
        data: {
          conversation_id: selectedId,
          website_id: selectedConversation?.website.id || "",
          visitor_id: selectedConversation?.visitor.id || "",
        },
      });
    }
    if (typingStopTimerRef.current) window.clearTimeout(typingStopTimerRef.current);
    typingStopTimerRef.current = window.setTimeout(() => {
      stopTeamTyping();
    }, 2200);
  };

  const selectedWebsiteSettings = bootstrap.websites.find((item) => item.id === selectedConversation?.website.id)?.widget_settings;
  const audioCallsEnabled = Boolean(callSettingsQuery.data?.enabled && selectedWebsiteSettings?.allow_audio_calls);
  const videoCallsEnabled = Boolean(callSettingsQuery.data?.enabled && callSettingsQuery.data?.allow_video && selectedWebsiteSettings?.allow_video_calls);
  const headerNotices = useMemo<ChatHeaderNotice[]>(() => {
    if (!error) return [];
    return [{ id: "support-error", tone: "danger", message: error }];
  }, [error]);

  return (
    <div
      className={`ms-conversation-view ms-support-conversation-view${selectedId ? " has-selection" : ""}${detailsOpen && selectedConversation ? " ms-conversation-view--details-open" : ""}`}
      aria-label="Support Chat inbox"
    >
      <aside className="ms-conversation-view__inbox ms-support-inbox__list">
        <header className="ms-support-inbox__list-header">
          <div><span>Support Chat</span><h1>Inbox</h1></div>
          {listQuery.data?.unread_total ? <strong aria-label={`${listQuery.data.unread_total} unread support messages`}>{Math.min(99, listQuery.data.unread_total)}</strong> : null}
        </header>
        <div className="ms-support-inbox__filters">
          <div className="ms-support-saved-view-row">
            <select aria-label="Saved inbox view" value={selectedSavedViewId} onChange={(event) => { setSelectedSavedViewId(event.target.value); applySavedView(event.target.value); }}>
              <option value="">Saved views</option>
              {savedViewsQuery.data?.map((view) => <option value={view.id} key={view.id}>{view.name}</option>)}
            </select>
            {selectedSavedViewId ? <button type="button" className="ms-support-saved-view-remove" disabled={removeViewMutation.isPending} onClick={() => { removeViewMutation.mutate(selectedSavedViewId); setSelectedSavedViewId(""); }}>Remove</button> : null}
            <details className="ms-support-save-view">
              <summary>Save</summary>
              <div>
                <input value={savedViewName} onChange={(event) => setSavedViewName(event.target.value)} placeholder="View name" maxLength={80} />
                <button type="button" disabled={!savedViewName.trim() || saveViewMutation.isPending} onClick={() => saveViewMutation.mutate()}>Save view</button>
              </div>
            </details>
          </div>
          <select
            aria-label="Website filter"
            value={website}
            onChange={(event) => {
              setWebsite(event.target.value);
              openConversation("");
            }}
          >
            <option value="">All websites</option>
            {bootstrap.websites.map((item) => {
              const unread = listQuery.data?.website_unread[item.id] || 0;
              return (
                <option value={item.id} key={item.id}>
                  {item.name}
                  {unread ? ` (${unread})` : ""}
                </option>
              );
            })}
          </select>
          <div className="ms-support-filter-pair">
            <select aria-label="Priority filter" value={priority} onChange={(event) => { setPriority(event.target.value); openConversation(""); }}>
              <option value="">All priorities</option>
              <option value="urgent">Urgent</option>
              <option value="high">High</option>
              <option value="normal">Normal</option>
              <option value="low">Low</option>
            </select>
            <select aria-label="Tag filter" value={tag} onChange={(event) => { setTag(event.target.value); openConversation(""); }}>
              <option value="">All tags</option>
              {tagsQuery.data?.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}
            </select>
          </div>
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search visitors"
            aria-label="Search support conversations"
          />
        </div>
        {alertsQuery.data?.results.length ? (
          <section className="ms-support-service-alerts" aria-label="Service alerts">
            <div className="ms-support-service-alerts__heading">
              <strong>Service alerts</strong>
              <button type="button" disabled={readAllAlertsMutation.isPending} onClick={() => readAllAlertsMutation.mutate()}>Mark all read</button>
            </div>
            <div className="ms-support-service-alerts__list">
              {alertsQuery.data.results.slice(0, 3).map((alert) => (
                <button type="button" key={alert.id} onClick={() => openServiceAlert(alert.id, alert.conversation_id)}>
                  <span>{String(alert.metadata.visitor_name || "Website visitor")}</span>
                  <strong>{alert.kind.replace(/_/g, " ")}</strong>
                  <small>{alert.website.name} · {formatServiceDeadline(alert.due_at)}</small>
                </button>
              ))}
            </div>
          </section>
        ) : null}
        <div
          className="ms-support-queue-tabs"
          role="tablist"
          aria-label="Support queues"
        >
          {QUEUES.filter(
            (item) => item.value !== "mine" || bootstrap.role === "agent",
          ).map((item) => (
            <button
              type="button"
              role="tab"
              aria-selected={queue === item.value}
              className={queue === item.value ? "is-active" : ""}
              onClick={() => {
                setQueue(item.value);
                openConversation("");
              }}
              key={item.value}
            >
              {item.label}
            </button>
          ))}
        </div>
        <div className="ms-support-conversation-list">
          {listQuery.isLoading ? (
            <div className="ms-support-inbox-state">Loading conversations…</div>
          ) : null}
          {listQuery.isError ? (
            <div className="ms-support-inbox-state is-error">
              <span>
                {
                parseApiError(
                  listQuery.error,
                  "Support conversations could not be loaded.",
                ).message
                }
              </span>
              <button
                type="button"
                disabled={listQuery.isFetching}
                onClick={() => void listQuery.refetch()}
              >
                {listQuery.isFetching ? "Retrying…" : "Retry"}
              </button>
            </div>
          ) : null}
          {!listQuery.isLoading && !listQuery.data?.results.length ? (
            <div className="ms-support-inbox-state">
              No conversations in this queue.
            </div>
          ) : null}
          {listQuery.data?.results.map((conversation) => (
            <ConversationRow
              conversation={conversation}
              active={selectedId === conversation.id}
              onOpen={() => openConversation(conversation.id)}
              key={conversation.id}
            />
          ))}
        </div>
      </aside>

      <span className="ms-conversation-resizer ms-conversation-resizer--inbox" aria-hidden="true" />

      <section className="ms-chat-surface">
        {!selectedConversation ? (
          <div className="ms-support-inbox-state ms-support-inbox-state--center">
            Select a support conversation.
          </div>
        ) : (
          <>
            <ChatHeader
              title={visitorName(selectedConversation)}
              subtitle={`${selectedConversation.website.name} · ${
                selectedConversation.visitor.is_online
                  ? "Online"
                  : selectedConversation.status.replace(/_/g, " ")
              }`}
              avatarPerson={{
                id: selectedConversation.visitor.id,
                username: "website-visitor",
                display_name: visitorName(selectedConversation),
                is_online: selectedConversation.visitor.is_online,
              }}
              notices={headerNotices}
              detailsOpen={detailsOpen}
              startingCallType={startCallMutation.isPending ? (startCallMutation.variables || null) : null}
              voiceCallEnabled={audioCallsEnabled && selectedConversation.status !== "closed"}
              videoCallEnabled={videoCallsEnabled && selectedConversation.status !== "closed"}
              actionsBefore={bootstrap.role === "agent" && !selectedConversation.assigned_agent ? (
                <button type="button" className="ms-button ms-button--ghost ms-button--compact ms-support-take-button" disabled={claimMutation.isPending} onClick={() => claimMutation.mutate()} aria-label="Take conversation" title="Take conversation">
                  Take
                </button>
              ) : null}
              onBack={() => openConversation("")}
              onToggleDetails={() => setDetailsOpen((value) => !value)}
              onStartVoiceCall={() => startCallMutation.mutate("voice")}
              onStartVideoCall={() => startCallMutation.mutate("video")}
            />
            <section
              className="ms-chat-timeline"
              ref={timelineRef}
              aria-label="Messages"
              aria-live="polite"
            >
              {messagesQuery.isLoading ? (
                <div className="ms-chat-state">Loading messages…</div>
              ) : null}
              {messagesQuery.data?.messages.map((message, index, messages) => {
                const previous = messages[index - 1];
                const next = messages[index + 1];
                const sameSender = (candidate?: SupportMessage) =>
                  Boolean(candidate && candidate.sender.kind === message.sender.kind);
                const groupedBefore = sameSender(previous);
                const groupedAfter = sameSender(next);
                const groupPosition = groupedBefore
                  ? (groupedAfter ? "middle" : "end")
                  : (groupedAfter ? "start" : "single");
                const showDate = !previous ||
                  new Date(previous.created_at).toDateString() !== new Date(message.created_at).toDateString();
                return (
                  <div className={`ms-message-block is-group-${groupPosition}${groupedBefore ? " is-group-continuation" : ""}`} key={message.id}>
                    {showDate ? <div className="ms-timeline-chip">{new Date(message.created_at).toLocaleDateString()}</div> : null}
                    <SupportMessageBubble message={message} groupPosition={groupPosition} />
                  </div>
                );
              })}
            </section>
            <footer className="ms-chat-composer-dock">
              <TypingIndicator names={Object.values(typingVisitors).filter(Boolean)} />
              <div className="ms-support-composer-shortcuts">
                <select
                  className="ms-support-canned-select"
                  aria-label="Insert canned reply"
                  value=""
                  onChange={(event) => {
                    const reply = cannedRepliesQuery.data?.find((item) => item.id === event.target.value);
                    if (reply) setComposerInsertion({ id: `${Date.now()}-${reply.id}`, text: reply.body });
                    event.target.value = "";
                  }}
                  disabled={selectedConversation.status === "closed" || sendMutation.isPending}
                >
                  <option value="">Quick replies</option>
                  {cannedRepliesQuery.data?.map((reply) => <option value={reply.id} key={reply.id}>{reply.shortcut} · {reply.title}</option>)}
                </select>
                <select
                  className="ms-support-canned-select ms-support-knowledge-select"
                  aria-label="Insert knowledge answer"
                  value=""
                  onChange={(event) => {
                    const article = knowledgeArticlesQuery.data?.find((item) => item.id === event.target.value);
                    if (article) setComposerInsertion({ id: `${Date.now()}-${article.id}`, text: article.body });
                    event.target.value = "";
                  }}
                  disabled={selectedConversation.status === "closed" || sendMutation.isPending}
                >
                  <option value="">Knowledge</option>
                  {knowledgeArticlesQuery.data?.map((article) => <option value={article.id} key={article.id}>{article.title}</option>)}
                </select>
              </div>
              <MessageComposer
                draftKey={`support:${selectedId}`}
                draftInsertion={composerInsertion}
                replyTo={null}
                onClearReply={() => undefined}
                editingMessage={null}
                onCancelEdit={() => undefined}
                allowViewOnce={false}
                disabled={selectedConversation.status === "closed"}
                disabledReason={selectedConversation.status === "closed" ? "This support conversation is closed." : null}
                onTyping={sendTyping}
                onUpload={async (file, options) => {
                  if (options.signal.aborted) throw new Error("Upload cancelled.");
                  const upload = await supportApi.uploadConversationFile(selectedId, file);
                  options.onProgress(100);
                  return {
                    uploadId: upload.id,
                    mediaKind: upload.media_kind,
                    width: upload.width || undefined,
                    height: upload.height || undefined,
                    rotation: upload.rotation || undefined,
                    durationSeconds: upload.duration_seconds || undefined,
                  };
                }}
                onSend={async (payload) => {
                  stopTeamTyping();
                  const clientTempId = String(payload.client_temp_id || Date.now());
                  const optimisticMessage = buildOptimisticSupportMessage(
                    bootstrap,
                    payload as Record<string, unknown>,
                  );
                  queryClient.setQueryData<SupportConversationMessagesResponse>(
                    ["support-conversation-messages", selectedId],
                    (current) => current
                      ? {
                          ...current,
                          messages: [...current.messages, optimisticMessage],
                          conversation: {
                            ...current.conversation,
                            last_message: optimisticMessage,
                            updated_at: optimisticMessage.created_at,
                          },
                        }
                      : current,
                  );
                  try {
                    await sendQueueRef.current.enqueue(() => sendMutation.mutateAsync({
                      text: String(payload.text || "").trim(),
                      attachment_ids: Array.isArray(payload.attachment_ids)
                        ? payload.attachment_ids.map(String)
                        : [],
                      clientTempId,
                      conversationId: selectedId,
                    }));
                  } catch (reason) {
                    throw new Error(parseApiError(reason, "The message could not be sent.").message);
                  }
                }}
                onSendVoiceNote={sendVoiceNote}
              />
            </footer>
          </>
        )}
      </section>

      {detailsOpen && selectedConversation ? (
        <>
          <span className="ms-conversation-resizer ms-conversation-resizer--details" aria-hidden="true" />
          <button type="button" className="ms-conversation-details-backdrop" onClick={() => setDetailsOpen(false)} aria-label="Close conversation details" />
          <div className="ms-conversation-view__details">
            <aside className="ms-support-shared-details">
            <header className="ms-support-details-header">
              <div><span>Conversation</span><strong>Details</strong></div>
              <button type="button" className="ms-icon-button" onClick={() => setDetailsOpen(false)} aria-label="Close visitor details">×</button>
            </header>
            <ConversationDetails
              conversation={selectedConversation}
              bootstrap={bootstrap}
              disabled={updateMutation.isPending}
              onUpdate={(payload) => updateMutation.mutate(payload)}
              defaultFollowUpMinutes={serviceSettingsQuery.data?.default_follow_up_minutes || 1440}
            />
            </aside>
          </div>
        </>
      ) : null}
      {activeCall ? <SupportGuestCall initialCall={activeCall} onFinished={() => { setActiveCall(null); queryClient.setQueryData(["support-active-call"], { call: null }); }} /> : null}
    </div>
  );
}
