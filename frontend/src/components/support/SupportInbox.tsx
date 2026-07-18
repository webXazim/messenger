import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { supportApi } from "../../api/support";
import { parseApiError } from "../../lib/apiErrors";
import type {
  SupportBootstrap,
  SupportConversation,
  SupportConversationFilters,
  SupportConversationUpdateInput,
  SupportMessage,
  SupportCall,
} from "../../types/support";
import { UserAvatar } from "../UserAvatar";
import { useSupportSocketStatus } from "../../hooks/useSupportRealtime";
import { SupportMessageMedia } from "./SupportMessageMedia";
import { SupportConversationTools } from "./SupportConversationTools";
import { VoiceNoteRecorder, type VoiceNotePayload } from "../VoiceNoteRecorder";
import { SupportGuestCall } from "./SupportGuestCall";

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

function MessageBubble({ message }: { message: SupportMessage }) {
  const teamMessage = message.sender.kind !== "visitor";
  return (
    <div
      className={`ms-support-message-row ${teamMessage ? "is-team" : "is-visitor"}`}
    >
      <div className="ms-support-message-meta">
        <strong>{message.sender.display_name}</strong>
        <time>{formatCompactTime(message.created_at)}</time>
      </div>
      <div
        className={`ms-support-message-bubble${message.is_own ? " is-own" : ""}`}
      >
        {message.text ? (
          <p className="ms-support-message-text">{message.text}</p>
        ) : null}
        <SupportMessageMedia
          attachments={message.attachments || []}
          voiceNote={message.voice_note}
        />
      </div>
    </div>
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
  const [draft, setDraft] = useState("");
  const [pendingUploads, setPendingUploads] = useState<
    Array<{ localId: string; file: File; uploadId?: string; error?: string }>
  >([]);
  const [uploadingCount, setUploadingCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [activeCall, setActiveCall] = useState<SupportCall | null>(null);
  const timelineRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

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
  const messagesQuery = useQuery({
    queryKey: ["support-conversation-messages", selectedId],
    queryFn: ({ signal }) =>
      supportApi.getConversationMessages(selectedId, signal),
    enabled: Boolean(selectedId),
    refetchInterval: selectedId && socketStatus !== "open" ? 4000 : false,
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
    setDraft("");
    setPendingUploads([]);
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
    }) => supportApi.sendConversationMessage(selectedId, payload),
    onMutate: () => setError(null),
    onSuccess: async () => {
      setDraft("");
      setPendingUploads([]);
      await refreshConversation();
    },
    onError: (mutationError) =>
      setError(
        parseApiError(mutationError, "The message could not be sent.").message,
      ),
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

  const addFiles = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    event.target.value = "";
    if (!selectedId || !files.length) return;
    const availableSlots = Math.max(0, 8 - pendingUploads.length);
    const accepted = files.slice(0, availableSlots);
    if (accepted.length < files.length)
      setError("A message can contain up to 8 attachments.");
    const queued = accepted.map((file) => ({
      localId: `${Date.now()}-${crypto.randomUUID?.() || Math.random()}`,
      file,
    }));
    setPendingUploads((current) => [...current, ...queued]);
    setUploadingCount((count) => count + queued.length);
    await Promise.all(
      queued.map(async (item) => {
        try {
          const upload = await supportApi.uploadConversationFile(
            selectedId,
            item.file,
          );
          setPendingUploads((current) =>
            current.map((candidate) =>
              candidate.localId === item.localId
                ? { ...candidate, uploadId: upload.id }
                : candidate,
            ),
          );
        } catch (uploadError) {
          const message = parseApiError(
            uploadError,
            `${item.file.name} could not be uploaded.`,
          ).message;
          setPendingUploads((current) =>
            current.map((candidate) =>
              candidate.localId === item.localId
                ? { ...candidate, error: message }
                : candidate,
            ),
          );
        } finally {
          setUploadingCount((count) => Math.max(0, count - 1));
        }
      }),
    );
  };

  const sendVoiceNote = async (payload: VoiceNotePayload) => {
    if (!selectedId) throw new Error("Select a conversation first.");
    setError(null);
    try {
      const upload = await supportApi.uploadConversationFile(
        selectedId,
        payload.file,
        {
          durationSeconds: payload.durationSeconds,
          waveform: payload.waveform,
        },
      );
      await supportApi.sendConversationMessage(selectedId, {
        attachment_ids: [upload.id],
        voice_note: true,
      });
      await refreshConversation();
    } catch (voiceError) {
      const message = parseApiError(
        voiceError,
        "The voice message could not be sent.",
      ).message;
      setError(message);
      throw new Error(message);
    }
  };

  const send = (event: FormEvent) => {
    event.preventDefault();
    const attachmentIds = pendingUploads.flatMap((item) =>
      item.uploadId ? [item.uploadId] : [],
    );
    if (
      !selectedId ||
      (!draft.trim() && !attachmentIds.length) ||
      uploadingCount > 0 ||
      sendMutation.isPending
    )
      return;
    sendMutation.mutate({ text: draft.trim(), attachment_ids: attachmentIds });
  };

  const selectedWebsiteSettings = bootstrap.websites.find((item) => item.id === selectedConversation?.website.id)?.widget_settings;
  const audioCallsEnabled = Boolean(callSettingsQuery.data?.enabled && selectedWebsiteSettings?.allow_audio_calls);
  const videoCallsEnabled = Boolean(callSettingsQuery.data?.enabled && callSettingsQuery.data?.allow_video && selectedWebsiteSettings?.allow_video_calls);
  const callsEnabled = audioCallsEnabled || videoCallsEnabled;

  return (
    <section
      className={`ms-support-inbox${selectedId ? " has-selection" : ""}`}
      aria-label="Support Chat inbox"
    >
      <aside className="ms-support-inbox__list">
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
              {
                parseApiError(
                  listQuery.error,
                  "Support conversations could not be loaded.",
                ).message
              }
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

      <main className="ms-support-inbox__conversation">
        {!selectedConversation ? (
          <div className="ms-support-inbox-state ms-support-inbox-state--center">
            Select a support conversation.
          </div>
        ) : (
          <>
            <header className="ms-support-conversation-header">
              <button
                type="button"
                className="ms-support-mobile-back"
                onClick={() => openConversation("")}
                aria-label="Back to support conversations"
              >
                ‹
              </button>
              <div>
                <strong>{visitorName(selectedConversation)}</strong>
                <span>
                  {selectedConversation.website.name} ·{" "}
                  {selectedConversation.status.replace(/_/g, " ")}
                </span>
              </div>
              {callsEnabled && selectedConversation.status !== "closed" ? (
                <div className="ms-support-call-actions" aria-label="Support call actions">
                  {audioCallsEnabled ? <button type="button" className="ms-support-call-action" disabled={startCallMutation.isPending || Boolean(activeCall)} onClick={() => startCallMutation.mutate("voice")} aria-label="Start audio call" title="Start audio call">☎</button> : null}
                  {videoCallsEnabled ? <button type="button" className="ms-support-call-action" disabled={startCallMutation.isPending || Boolean(activeCall)} onClick={() => startCallMutation.mutate("video")} aria-label="Start video call" title="Start video call">▣</button> : null}
                </div>
              ) : null}
              <span className={`ms-support-live-state is-${socketStatus}`}>
                {socketStatus === "open" ? "Live" : "Reconnecting"}
              </span>
              {bootstrap.role === "agent" &&
              !selectedConversation.assigned_agent ? (
                <button
                  type="button"
                  className="ms-button ms-button--ghost ms-button--compact"
                  disabled={claimMutation.isPending}
                  onClick={() => claimMutation.mutate()}
                >
                  Take conversation
                </button>
              ) : null}
            </header>
            <details className="ms-support-mobile-details">
              <summary>Visitor and conversation details</summary>
              <ConversationDetails
                conversation={selectedConversation}
                bootstrap={bootstrap}
                disabled={updateMutation.isPending}
                onUpdate={(payload) => updateMutation.mutate(payload)}
                defaultFollowUpMinutes={serviceSettingsQuery.data?.default_follow_up_minutes || 1440}
              />
            </details>
            <div
              className="ms-support-message-timeline"
              ref={timelineRef}
              aria-live="polite"
            >
              {messagesQuery.isLoading ? (
                <div className="ms-support-inbox-state">Loading messages…</div>
              ) : null}
              {messagesQuery.data?.messages.map((message) => (
                <MessageBubble message={message} key={message.id} />
              ))}
            </div>
            {error ? (
              <div className="ms-page-error" role="alert">
                {error}
              </div>
            ) : null}
            <form className="ms-support-composer" onSubmit={send}>
              {pendingUploads.length ? (
                <div
                  className="ms-support-composer-uploads"
                  aria-label="Pending attachments"
                >
                  {pendingUploads.map((item) => (
                    <span
                      className={`ms-support-upload-chip${item.error ? " is-error" : ""}`}
                      key={item.localId}
                    >
                      <span>
                        <strong>{item.file.name}</strong>
                        <small>
                          {item.error ||
                            (item.uploadId ? "Ready" : "Uploading…")}
                        </small>
                      </span>
                      <button
                        type="button"
                        onClick={() =>
                          setPendingUploads((current) =>
                            current.filter(
                              (candidate) => candidate.localId !== item.localId,
                            ),
                          )
                        }
                        aria-label={`Remove ${item.file.name}`}
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>
              ) : null}
              <div className="ms-support-composer-row">
                <input
                  ref={fileInputRef}
                  className="ms-support-file-input"
                  type="file"
                  multiple
                  onChange={addFiles}
                  disabled={
                    selectedConversation.status === "closed" ||
                    sendMutation.isPending
                  }
                />
                <button
                  type="button"
                  className="ms-support-composer-tool"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={
                    selectedConversation.status === "closed" ||
                    sendMutation.isPending ||
                    pendingUploads.length >= 8
                  }
                  aria-label="Attach files"
                  title="Attach files"
                >
                  +
                </button>
                <select
                  className="ms-support-canned-select"
                  aria-label="Insert canned reply"
                  value=""
                  onChange={(event) => {
                    const reply = cannedRepliesQuery.data?.find((item) => item.id === event.target.value);
                    if (reply) setDraft((current) => current ? `${current}
${reply.body}` : reply.body);
                    event.target.value = "";
                  }}
                  disabled={selectedConversation.status === "closed" || sendMutation.isPending}
                >
                  <option value="">Replies</option>
                  {cannedRepliesQuery.data?.map((reply) => <option value={reply.id} key={reply.id}>{reply.shortcut} · {reply.title}</option>)}
                </select>
                <select
                  className="ms-support-canned-select ms-support-knowledge-select"
                  aria-label="Insert knowledge answer"
                  value=""
                  onChange={(event) => {
                    const article = knowledgeArticlesQuery.data?.find((item) => item.id === event.target.value);
                    if (article) setDraft((current) => current ? `${current}
${article.body}` : article.body);
                    event.target.value = "";
                  }}
                  disabled={selectedConversation.status === "closed" || sendMutation.isPending}
                >
                  <option value="">Knowledge</option>
                  {knowledgeArticlesQuery.data?.map((article) => <option value={article.id} key={article.id}>{article.title}</option>)}
                </select>
                <textarea
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  placeholder="Reply to this visitor"
                  rows={1}
                  disabled={
                    selectedConversation.status === "closed" ||
                    sendMutation.isPending
                  }
                />
                <VoiceNoteRecorder
                  onSendVoiceNote={sendVoiceNote}
                  variant="inline"
                  disabled={
                    selectedConversation.status === "closed" ||
                    sendMutation.isPending ||
                    uploadingCount > 0
                  }
                />
                <button
                  type="submit"
                  className="ms-button ms-button--primary"
                  disabled={
                    (!draft.trim() &&
                      !pendingUploads.some((item) => item.uploadId)) ||
                    uploadingCount > 0 ||
                    pendingUploads.some((item) => item.error) ||
                    selectedConversation.status === "closed" ||
                    sendMutation.isPending
                  }
                >
                  {sendMutation.isPending
                    ? "Sending…"
                    : uploadingCount > 0
                      ? "Uploading…"
                      : "Send"}
                </button>
              </div>
            </form>
          </>
        )}
      </main>

      <aside className="ms-support-inbox__details">
        {selectedConversation ? (
          <ConversationDetails
            conversation={selectedConversation}
            bootstrap={bootstrap}
            disabled={updateMutation.isPending}
            onUpdate={(payload) => updateMutation.mutate(payload)}
            defaultFollowUpMinutes={serviceSettingsQuery.data?.default_follow_up_minutes || 1440}
          />
        ) : null}
      </aside>
      {activeCall ? <SupportGuestCall initialCall={activeCall} onFinished={() => { setActiveCall(null); queryClient.setQueryData(["support-active-call"], { call: null }); }} /> : null}
    </section>
  );
}
