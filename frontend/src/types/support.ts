export type SupportAccess =
  "disabled" | "upgrade_required" | "active" | "restricted";
export type SupportRole = "owner" | "agent" | null;
export type SupportAvailability = "available" | "busy" | "away" | "offline";

export type SupportOwner = {
  id: string;
  username: string;
  display_name: string;
  email?: string;
  avatar?: string | null;
};

export type SupportAccount = {
  id: string;
  owner: SupportOwner;
  status: string;
  plan_code: string;
  website_limit: number;
  agent_limit: number;
  current_period_end?: string | null;
  grace_ends_at?: string | null;
  access_active: boolean;
  created_at?: string;
  updated_at?: string;
};

export type SupportLimit = {
  used: number;
  limit: number;
  active?: number;
  pending?: number;
};

export type SupportWidgetSettings = {
  brand_name: string;
  primary_color: string;
  welcome_text: string;
  offline_text: string;
  launcher_text: string;
  privacy_note: string;
  position: "right" | "left";
  theme: "auto" | "light" | "dark";
  require_name: boolean;
  require_email: boolean;
  allow_attachments: boolean;
  allow_audio_calls: boolean;
  allow_video_calls: boolean;
  updated_at?: string;
};

export type SupportWebsite = {
  id: string;
  name: string;
  domain: string;
  site_key: string;
  allowed_origins: string[];
  widget_enabled: boolean;
  widget_settings: SupportWidgetSettings;
  install_code: string;
  is_active: boolean;
  created_at?: string;
  updated_at?: string;
};

export type SupportAgentUser = {
  id: string;
  username: string;
  email?: string;
  display_name: string;
  avatar?: string | null;
};

export type SupportAgent = {
  id: string;
  user: SupportAgentUser;
  availability: SupportAvailability;
  max_active_conversations: number;
  can_view_all_conversations: boolean;
  can_assign_conversations: boolean;
  can_view_analytics: boolean;
  can_manage_websites: boolean;
  can_manage_knowledge: boolean;
  can_manage_teams: boolean;
  can_manage_automations: boolean;
  can_export_data: boolean;
  is_active: boolean;
  assigned_website_ids: string[];
  team_ids: string[];
  active_conversation_count: number;
  joined_at?: string;
};

export type SupportInvitationWebsite = {
  id: string;
  name: string;
  domain: string;
};

export type SupportAgentInvitation = {
  id: string;
  email: string;
  status: "pending" | "accepted" | "revoked" | "expired";
  expires_at: string;
  last_sent_at: string;
  send_count: number;
  email_delivery_status: "queued" | "sent" | "failed";
  email_delivery_error?: string;
  email_delivered_at?: string | null;
  max_active_conversations: number;
  can_view_all_conversations: boolean;
  can_assign_conversations: boolean;
  can_view_analytics: boolean;
  can_manage_websites: boolean;
  can_manage_knowledge: boolean;
  can_manage_teams: boolean;
  can_manage_automations: boolean;
  can_export_data: boolean;
  invited_by?: SupportOwner | null;
  assigned_website_ids: string[];
  assigned_websites: SupportInvitationWebsite[];
  assigned_team_ids: string[];
  created_at?: string;
  updated_at?: string;
};

export type SupportTeam = {
  id: string;
  name: string;
  description: string;
  default_max_active_conversations: number;
  is_active: boolean;
  agent_ids: string[];
  website_ids: string[];
  agent_count: number;
};


export type SupportRoutingPolicy = {
  website_id: string; website_name: string; mode: "manual" | "round_robin" | "least_busy";
  least_busy_tiebreaker: boolean; overflow_behavior: "leave_unassigned" | "least_busy" | "notify_owner";
  offline_reassignment_minutes: number; prefer_previous_agent: boolean; enabled: boolean; updated_at?: string;
};
export type SupportRoutingPolicyInput = Omit<SupportRoutingPolicy, "website_id" | "website_name" | "updated_at">;

export type SupportTeamInput = {
  name: string;
  description?: string;
  default_max_active_conversations: number;
  agent_ids: string[];
  website_ids: string[];
  is_active?: boolean;
};

export type SupportBootstrap = {
  feature_enabled: boolean;
  access: SupportAccess;
  role: SupportRole;
  account: SupportAccount | null;
  limits: {
    websites: SupportLimit;
    agents: SupportLimit;
  } | null;
  websites: SupportWebsite[];
  agents: SupportAgent[];
  teams: SupportTeam[];
  invitations: SupportAgentInvitation[];
};

export type SupportWebsiteUsage = {
  website_id: string;
  conversations_today: number;
  messages_today: number;
  active_agents: number;
  average_resolution_seconds?: number | null;
  generated_at: string;
};

export type SupportWebsiteInput = {
  name: string;
  domain: string;
  allowed_origins?: string[];
  widget_enabled?: boolean;
};

export type SupportAgentInvitationInput = {
  email: string;
  website_ids: string[];
  team_ids?: string[];
  max_active_conversations: number;
  can_view_all_conversations: boolean;
  can_assign_conversations: boolean;
  can_view_analytics: boolean;
  can_manage_websites?: boolean;
  can_manage_knowledge?: boolean;
  can_manage_teams?: boolean;
  can_manage_automations?: boolean;
  can_export_data?: boolean;
};

export type SupportAgentUpdateInput = Omit<
  SupportAgentInvitationInput,
  "email"
>;

export type SupportInvitationPreview = {
  valid: boolean;
  status: "pending" | "accepted" | "revoked" | "expired";
  invited_email: string;
  inviter?: SupportOwner | null;
  websites: SupportInvitationWebsite[];
  expires_at: string;
  account_access_active: boolean;
};

export type SupportWidgetSettingsInput = Partial<
  Omit<SupportWidgetSettings, "updated_at">
>;

export type SupportPendingUpload = {
  id: string;
  media_kind: "image" | "video" | "audio" | "file";
  original_name: string;
  mime_type: string;
  size: number;
  width?: number | null;
  height?: number | null;
  rotation?: number | null;
  duration_seconds?: number | null;
  status: string;
  scan_status: string;
  scan_notes?: string;
  expires_at: string;
};

export type SupportAttachment = {
  id: string;
  media_kind: "image" | "video" | "audio" | "file";
  original_name: string;
  mime_type: string;
  size: number;
  width?: number | null;
  height?: number | null;
  rotation?: number | null;
  duration_seconds?: number | null;
  scan_status: string;
  can_preview_inline: boolean;
  download_url: string;
  preview_url?: string | null;
  thumbnail_url?: string | null;
};

export type SupportMessageSendInput = {
  client_temp_id?: string;
  text?: string;
  attachment_ids?: string[];
  voice_note?: boolean;
};

export type SupportMessageSender = {
  kind: "visitor" | "owner" | "agent" | "system";
  id?: string | null;
  username?: string;
  display_name: string;
  avatar?: string | null;
};

export type SupportMessage = {
  id: string;
  client_temp_id?: string;
  type: "text" | "image" | "video" | "audio" | "file" | "system";
  text: string;
  created_at: string;
  updated_at: string;
  delivery_status: "pending" | "sent" | "failed";
  receipt_status: "pending" | "sent" | "delivered" | "read" | "failed";
  delivered_at?: string | null;
  read_at?: string | null;
  sender: SupportMessageSender;
  is_own: boolean;
  voice_note: boolean;
  attachments: SupportAttachment[];
  preview_text: string;
};

export type SupportConversationWebsite = {
  id: string;
  name: string;
  domain: string;
};

export type SupportVisitor = {
  id: string;
  external_id: string;
  name: string;
  email: string;
  locale: string;
  current_page_url: string;
  referrer: string;
  last_seen_at: string;
  is_online: boolean;
};

export type SupportServiceSnapshot = {
  state: "on_track" | "due_soon" | "overdue" | "complete" | "none";
  active_target?: "first_response" | "next_response" | "resolution" | null;
  active_due_at?: string | null;
  is_overdue: boolean;
  is_due_soon: boolean;
  overdue_targets: string[];
  first_response_due_at?: string | null;
  next_response_due_at?: string | null;
  resolution_due_at?: string | null;
  first_response_breached_at?: string | null;
  next_response_breached_at?: string | null;
  resolution_breached_at?: string | null;
  follow_up_at?: string | null;
  follow_up_note: string;
  follow_up_due: boolean;
  follow_up_completed_at?: string | null;
  follow_up_created_by?: SupportOwner | null;
};

export type SupportConversation = {
  id: string;
  website: SupportConversationWebsite;
  visitor: SupportVisitor;
  assigned_agent: SupportAgent | null;
  status:
    | "new"
    | "open"
    | "waiting_customer"
    | "waiting_team"
    | "resolved"
    | "closed";
  priority: "low" | "normal" | "high" | "urgent";
  subject: string;
  first_response_at?: string | null;
  last_visitor_message_at?: string | null;
  last_agent_message_at?: string | null;
  resolved_at?: string | null;
  closed_at?: string | null;
  created_at: string;
  updated_at: string;
  last_message: SupportMessage | null;
  unread_count: number;
  visitor_unread_count: number;
  tags: SupportTag[];
  service: SupportServiceSnapshot;
  csat?: SupportCSATSurvey | null;
};

export type SupportConversationListResponse = {
  results: SupportConversation[];
  count: number;
  next_offset: number | null;
  next_cursor?: string | null;
  unread_total: number;
  website_unread: Record<string, number>;
};

export type SupportConversationMessagesResponse = {
  conversation: SupportConversation;
  messages: SupportMessage[];
};

export type SupportConversationFilters = {
  website?: string;
  queue?: "open" | "mine" | "unassigned" | "overdue" | "follow_up" | "resolved" | "closed";
  search?: string;
  status?: SupportConversation["status"] | "";
  priority?: SupportConversation["priority"] | "";
  cursor?: string;
  tag?: string;
  limit?: number;
  offset?: number;
};

export type SupportConversationUpdateInput = {
  status?: SupportConversation["status"];
  priority?: SupportConversation["priority"];
  assigned_agent_id?: string | null;
  follow_up_at?: string | null;
  follow_up_note?: string;
};

export type SupportUnreadSummary = {
  unread_total: number;
  website_unread: Record<string, number>;
  alert_unread: number;
};


export type SupportTag = {
  id: string;
  name: string;
  color: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type SupportInternalNote = {
  id: string;
  body: string;
  author: SupportOwner;
  created_at: string;
};

export type SupportCannedReply = {
  id: string;
  website_id?: string | null;
  website_name?: string | null;
  shortcut: string;
  title: string;
  body: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type SupportSavedInboxView = {
  id: string;
  name: string;
  website_id?: string | null;
  queue: string;
  status: string;
  priority: string;
  tag_id?: string | null;
  search: string;
  is_default: boolean;
  created_at: string;
  updated_at: string;
};

export type SupportAuditEvent = {
  id: string;
  action: string;
  summary: string;
  target_type: string;
  target_id?: string | null;
  metadata: Record<string, unknown>;
  actor: {
    id?: string | null;
    username?: string;
    display_name: string;
    avatar?: string | null;
  };
  created_at: string;
};

export type SupportConversationActivity = {
  events: SupportAuditEvent[];
  notes: SupportInternalNote[];
};

export type SupportBusinessDay = {
  enabled: boolean;
  start: string;
  end: string;
};

export type SupportServiceTargets = Record<"low" | "normal" | "high" | "urgent", number>;

export type SupportServiceSettings = {
  timezone: string;
  business_hours_enabled: boolean;
  business_hours: Record<
    "monday" | "tuesday" | "wednesday" | "thursday" | "friday" | "saturday" | "sunday",
    SupportBusinessDay
  >;
  first_response_targets: SupportServiceTargets;
  next_response_targets: SupportServiceTargets;
  resolution_targets: SupportServiceTargets;
  due_soon_minutes: number;
  default_follow_up_minutes: number;
  alert_owner: boolean;
  alert_assigned_agent: boolean;
  pause_while_waiting_customer: boolean;
  pause_resolution_while_snoozed: boolean;
  escalate_on_breach: boolean;
  escalation_team?: string | null;
  updated_at?: string;
};

export type SupportSlaPolicy = {
  id: string;
  name: string;
  website: string | null;
  website_name?: string | null;
  team: string | null;
  team_name?: string | null;
  is_active: boolean;
  first_response_targets: SupportServiceTargets;
  next_response_targets: SupportServiceTargets;
  resolution_targets: SupportServiceTargets;
  due_soon_minutes: number | null;
  pause_while_waiting_customer: boolean | null;
  pause_resolution_while_snoozed: boolean | null;
  alert_owner: boolean | null;
  alert_assigned_agent: boolean | null;
  escalate_on_breach: boolean | null;
  escalation_team: string | null;
  escalation_team_name?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type SupportSlaPolicyInput = Omit<
  SupportSlaPolicy,
  "id" | "website_name" | "team_name" | "escalation_team_name" | "created_at" | "updated_at"
>;

export type SupportServiceAlert = {
  id: string;
  kind: string;
  status: "unread" | "read" | "resolved";
  due_at: string;
  triggered_at: string;
  read_at?: string | null;
  conversation_id: string;
  website: SupportConversationWebsite;
  metadata: Record<string, unknown>;
};

export type SupportServiceAlertList = {
  results: SupportServiceAlert[];
  unread_count: number;
};

export type SupportFeedbackSettings = {
  csat_enabled: boolean;
  auto_request_on_resolve: boolean;
  allow_comment: boolean;
  survey_expiry_days: number;
  updated_at?: string;
};

export type SupportCSATSurvey = {
  id: string;
  status: "pending" | "submitted" | "dismissed" | "expired";
  source: "auto" | "manual";
  rating?: number | null;
  comment: string;
  available: boolean;
  allow_comment: boolean;
  requested_at: string;
  expires_at: string;
  submitted_at?: string | null;
};

export type SupportAnalyticsSummary = {
  conversations_created: number;
  resolved: number;
  resolution_rate: number;
  current_open: number;
  current_unassigned: number;
  current_overdue: number;
  median_first_response_seconds?: number | null;
  median_resolution_seconds?: number | null;
  sla_breach_rate: number;
  messages: number;
  visitor_messages: number;
  team_messages: number;
  csat_average?: number | null;
  csat_responses: number;
  csat_response_rate: number;
};

export type SupportAnalyticsDailyPoint = {
  date: string;
  created: number;
  resolved: number;
  messages: number;
  csat_responses: number;
  csat_average?: number | null;
};

export type SupportAnalyticsWebsiteRow = {
  website: SupportConversationWebsite;
  conversations: number;
  resolved: number;
  resolution_rate: number;
  median_first_response_seconds?: number | null;
  median_resolution_seconds?: number | null;
  sla_breach_rate: number;
  csat_average?: number | null;
  csat_response_rate: number;
};

export type SupportAnalyticsAgentRow = {
  agent: {
    id: string;
    user_id: string;
    display_name: string;
    username: string;
    avatar?: string | null;
  };
  availability: SupportAvailability;
  active_assigned: number;
  assigned_in_period: number;
  resolved_in_period: number;
  team_messages: number;
  median_first_response_seconds?: number | null;
  median_resolution_seconds?: number | null;
};

export type SupportAnalyticsOverview = {
  period: { start: string; end: string; days: number; website_id?: string | null };
  summary: SupportAnalyticsSummary;
  status_counts: Record<SupportConversation["status"], number>;
  daily: SupportAnalyticsDailyPoint[];
  websites: SupportAnalyticsWebsiteRow[];
  agents: SupportAnalyticsAgentRow[];
};


export type SupportNotificationSettings = {
  new_conversation: boolean;
  assignment_changed: boolean;
  sla_due_soon: boolean;
  sla_breached: boolean;
  internal_mention: boolean;
  follow_up_due: boolean;
  daily_summary: boolean;
  daily_summary_hour: number;
};

export type SupportSecuritySettings = {
  require_verified_identity_for_sensitive_actions: boolean;
  block_unverified_attachments: boolean;
  max_attachment_mb: number;
  allowed_attachment_extensions: string[];
  retain_audit_days: number;
  webhook_failure_disable_threshold: number;
  agent_session_timeout_minutes: number;
};

export type SupportAutomationRule = {
  id: string;
  name: string;
  description: string;
  trigger: string;
  conditions: Array<Record<string, unknown>>;
  actions: Array<Record<string, unknown>>;
  is_active: boolean;
  priority: number;
  stop_processing: boolean;
  execution_limit: number;
  created_at?: string;
  updated_at?: string;
};

export type SupportAutomationRuleInput = Omit<
  SupportAutomationRule,
  "id" | "created_at" | "updated_at"
>;

export type SupportAutomationExecution = {
  id: string;
  rule: { id: string; name: string };
  conversation_id?: string | null;
  trigger: string;
  status: "started" | "succeeded" | "skipped" | "failed";
  actions_executed: number;
  duration_ms: number;
  error?: string;
  created_at: string;
};

export type SupportAnalyticsV2Filters = {
  days?: number;
  start?: string;
  end?: string;
  website?: string;
  team?: string;
  agent?: string;
};

export type SupportAnalyticsV2MetricSet = {
  conversations: number;
  resolved: number;
  resolution_rate: number;
  first_response_seconds?: number | null;
  resolution_seconds?: number | null;
  sla_compliance: number;
  csat_average?: number | null;
  unassigned_rate: number;
  queue: {
    open: number;
    unassigned: number;
    overdue: number;
    at_risk: number;
  };
};

export type SupportAnalyticsV2Overview = {
  period: { start: string; end: string; days: number };
  filters: Record<string, string | null>;
  current: SupportAnalyticsV2MetricSet;
  previous: SupportAnalyticsV2MetricSet;
};

export type SupportAnalyticsV2VolumePoint = {
  date: string;
  created: number;
  resolved: number;
  messages: number;
};

export type SupportAnalyticsV2Volume = {
  period: { start: string; end: string; days: number };
  current: SupportAnalyticsV2VolumePoint[];
  previous: SupportAnalyticsV2VolumePoint[];
};

export type SupportAnalyticsV2WebsiteRow = {
  website: SupportConversationWebsite;
  conversations: number;
  resolved: number;
  first_response_seconds?: number | null;
  resolution_seconds?: number | null;
  sla_compliance: number;
  csat_average?: number | null;
};

export type SupportAnalyticsV2AgentRow = {
  agent: { id: string; display_name: string };
  availability: SupportAvailability;
  conversations: number;
  resolved: number;
  replies: number;
  first_response_seconds?: number | null;
  resolution_seconds?: number | null;
  csat_average?: number | null;
};

export type SupportAnalyticsV2TagRow = {
  tag: { id: string; name: string; color: string };
  conversations: number;
  share: number;
};

export type SupportAnalyticsV2HourRow = {
  hour: number;
  conversations: number;
};

export type SupportAnalyticsExport = {
  id: string;
  status: "queued" | "processing" | "ready" | "failed";
  format: string;
  filters?: Record<string, unknown>;
  download_ready?: boolean;
  created_at?: string;
  completed_at?: string | null;
  error_message?: string;
};

export type SupportKnowledgeSettings = {
  enabled: boolean;
  show_in_widget: boolean;
  suggestions_enabled: boolean;
  allow_article_feedback: boolean;
  max_suggestions: number;
  updated_at: string;
};

export type SupportKnowledgeCategory = {
  id: string;
  name: string;
  description: string;
  sort_order: number;
  is_active: boolean;
  article_count?: number;
  created_at: string;
  updated_at: string;
};

export type SupportKnowledgeArticle = {
  id: string;
  category?: string | null;
  category_name?: string | null;
  title: string;
  slug: string;
  summary: string;
  seo_description: string;
  language: string;
  body: string;
  status: "draft" | "published" | "archived";
  all_websites: boolean;
  is_featured: boolean;
  website_ids: string[];
  website_names: string[];
  published_at?: string | null;
  view_count: number;
  helpful_count: number;
  not_helpful_count: number;
  helpful_rate?: number | null;
  related_articles: Array<{ id: string; title: string }>;
  revision_count: number;
  created_by?: SupportOwner | null;
  updated_by?: SupportOwner | null;
  created_at: string;
  updated_at: string;
};

export type SupportKnowledgeArticleInput = {
  category_id?: string | null;
  title: string;
  summary?: string;
  seo_description?: string;
  language?: string;
  body: string;
  status: SupportKnowledgeArticle["status"];
  all_websites: boolean;
  website_ids: string[];
  is_featured: boolean;
  related_article_ids?: string[];
  change_note?: string;
};

export type SupportKnowledgeRevision = {
  id: string;
  version: number;
  title: string;
  summary: string;
  seo_description: string;
  language: string;
  status: SupportKnowledgeArticle["status"];
  category_name: string;
  all_websites: boolean;
  website_ids: string[];
  is_featured: boolean;
  change_note: string;
  created_by?: SupportOwner | null;
  created_at: string;
};

export type SupportPrivacySettings = {
  retention_enabled: boolean;
  resolved_conversation_retention_days: number;
  widget_session_retention_days: number;
  export_retention_days: number;
  allow_visitor_deletion_requests: boolean;
  include_attachments_in_exports: boolean;
  updated_at: string;
};

export type SupportWebhookEndpoint = {
  id: string;
  name: string;
  url: string;
  event_types: string[];
  is_active: boolean;
  failure_count: number;
  last_delivery_at?: string | null;
  last_success_at?: string | null;
  last_failure_at?: string | null;
  created_at: string;
  updated_at: string;
  signing_secret?: string;
  secret_notice?: string;
};

export type SupportWebhookDelivery = {
  id: string;
  endpoint: string;
  endpoint_name: string;
  event_type: string;
  event_id: string;
  status: "pending" | "processing" | "succeeded" | "failed";
  attempt_count: number;
  next_attempt_at: string;
  response_status?: number | null;
  response_body: string;
  error: string;
  delivered_at?: string | null;
  created_at: string;
};

export type SupportDataExport = {
  id: string;
  status: "pending" | "processing" | "ready" | "failed" | "expired";
  file_size: number;
  record_counts: Record<string, number>;
  include_attachments: boolean;
  started_at?: string | null;
  completed_at?: string | null;
  expires_at: string;
  error: string;
  download_url?: string | null;
  created_at: string;
};

export type SupportVisitorDeletionRequest = {
  id: string;
  website: string;
  website_name: string;
  visitor_external_id: string;
  source: "owner" | "visitor" | "retention";
  status: "pending" | "processing" | "completed" | "failed";
  requested_at: string;
  completed_at?: string | null;
  error: string;
};

export type SupportCallParticipant = {
  kind: "team" | "visitor";
  state: "ringing" | "joined" | "declined" | "missed" | "left";
  audio_enabled: boolean;
  video_enabled: boolean;
  joined_at?: string | null;
  left_at?: string | null;
};

export type SupportCallSignal = {
  id: string;
  signal_id: string;
  signal_type: "offer" | "answer" | "ice_candidate" | "renegotiate" | "ice_restart" | "hangup" | "media_toggle" | "network_state";
  payload: Record<string, unknown>;
  sender_kind: "team" | "visitor";
  created_at: string;
};

export type SupportCall = {
  id: string;
  conversation_id: string;
  website_id: string;
  website_name: string;
  visitor_id: string;
  visitor_name: string;
  initiated_by: SupportOwner;
  initiator_kind: "team" | "visitor";
  call_type: "voice" | "video";
  status: "ringing" | "ongoing" | "declined" | "missed" | "ended" | "failed";
  started_at: string;
  answered_at?: string | null;
  ended_at?: string | null;
  ended_reason?: string;
  participants: SupportCallParticipant[];
  pending_signals?: SupportCallSignal[];
};

export type SupportCallSettings = {
  enabled: boolean;
  allow_video: boolean;
  max_duration_minutes: number;
  updated_at?: string;
};

export type SupportTurnCredentials = {
  configured: boolean;
  ttl_seconds: number;
  username?: string;
  credential?: string;
  credential_type?: string;
  ice_servers: RTCIceServer[];
};
