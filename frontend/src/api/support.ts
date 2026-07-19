import { http } from "../lib/http";
import { unwrapData } from "../lib/apiResponse";
import type {
  SupportAgent,
  SupportAgentInvitation,
  SupportAgentInvitationInput,
  SupportAgentUpdateInput,
  SupportAvailability,
  SupportBootstrap,
  SupportConversation,
  SupportConversationFilters,
  SupportConversationListResponse,
  SupportConversationMessagesResponse,
  SupportConversationUpdateInput,
  SupportMessage,
  SupportMessageSendInput,
  SupportPendingUpload,
  SupportInvitationPreview,
  SupportWebsite,
  SupportWebsiteInput,
  SupportWidgetSettings,
  SupportWidgetSettingsInput,
  SupportUnreadSummary,
  SupportTag,
  SupportInternalNote,
  SupportCannedReply,
  SupportSavedInboxView,
  SupportConversationActivity,
  SupportServiceAlert,
  SupportServiceAlertList,
  SupportServiceSettings,
  SupportFeedbackSettings,
  SupportCSATSurvey,
  SupportAnalyticsOverview,
  SupportKnowledgeSettings,
  SupportKnowledgeCategory,
  SupportKnowledgeArticle,
  SupportKnowledgeArticleInput,
  SupportPrivacySettings,
  SupportWebhookEndpoint,
  SupportWebhookDelivery,
  SupportDataExport,
  SupportVisitorDeletionRequest,
  SupportCall,
  SupportCallSettings,
  SupportCallSignal,
  SupportTurnCredentials,
} from "../types/support";

export const supportApi = {

  async activatePlan(planCode: string) {
    const response = await http.post("/support/plans/activate/", { plan_code: planCode });
    return unwrapData<{ message: string; trial_ends_at: string }>(response.data);
  },

  async getCallSettings(signal?: AbortSignal) {
    const response = await http.get("/support/call-settings/", { signal });
    return unwrapData<SupportCallSettings>(response.data);
  },

  async updateCallSettings(payload: Partial<SupportCallSettings>) {
    const response = await http.patch("/support/call-settings/", payload);
    return unwrapData<SupportCallSettings>(response.data);
  },

  async getActiveCall(signal?: AbortSignal) {
    const response = await http.get("/support/calls/active/", { signal });
    return unwrapData<{ call: SupportCall | null }>(response.data);
  },

  async getActiveConversationCall(conversationId: string, signal?: AbortSignal) {
    const response = await http.get(`/support/conversations/${conversationId}/calls/`, { signal });
    return unwrapData<{ call: SupportCall | null }>(response.data);
  },

  async startConversationCall(conversationId: string, callType: "voice" | "video") {
    const response = await http.post(`/support/conversations/${conversationId}/calls/`, { call_type: callType });
    return unwrapData<SupportCall>(response.data);
  },

  async getCall(callId: string, signal?: AbortSignal) {
    const response = await http.get(`/support/calls/${callId}/`, { signal });
    return unwrapData<SupportCall>(response.data);
  },

  async endCall(callId: string, reason = "ended") {
    const response = await http.post(`/support/calls/${callId}/end/`, { reason });
    return unwrapData<SupportCall>(response.data);
  },

  async acceptCall(callId: string) {
    const response = await http.post(`/support/calls/${callId}/accept/`);
    return unwrapData<SupportCall>(response.data);
  },

  async declineCall(callId: string, reason = "declined") {
    const response = await http.post(`/support/calls/${callId}/decline/`, { reason });
    return unwrapData<SupportCall>(response.data);
  },

  async listCallSignals(callId: string, signal?: AbortSignal) {
    const response = await http.get(`/support/calls/${callId}/signals/`, { signal });
    return unwrapData<{ signals: SupportCallSignal[] }>(response.data);
  },

  async sendCallSignal(callId: string, signalType: SupportCallSignal["signal_type"], payload: Record<string, unknown>) {
    const response = await http.post(`/support/calls/${callId}/signals/`, { signal_type: signalType, payload });
    return unwrapData<SupportCallSignal>(response.data);
  },

  async updateCallMedia(callId: string, payload: { audio_enabled?: boolean; video_enabled?: boolean }) {
    const response = await http.patch(`/support/calls/${callId}/media-state/`, payload);
    return unwrapData<SupportCall>(response.data);
  },

  async getCallTurnCredentials(signal?: AbortSignal) {
    const response = await http.get("/support/calls/turn-credentials/", { signal });
    return unwrapData<SupportTurnCredentials>(response.data);
  },

  async getPrivacySettings(signal?: AbortSignal) {
    const response = await http.get("/support/privacy/settings/", { signal });
    return unwrapData<SupportPrivacySettings>(response.data);
  },

  async updatePrivacySettings(payload: Partial<SupportPrivacySettings>) {
    const response = await http.patch("/support/privacy/settings/", payload);
    return unwrapData<SupportPrivacySettings>(response.data);
  },

  async listWebhooks(signal?: AbortSignal) {
    const response = await http.get("/support/webhooks/", { signal });
    return unwrapData<{ supported_events: string[]; endpoints: SupportWebhookEndpoint[] }>(response.data);
  },

  async createWebhook(payload: { name: string; url: string; event_types: string[]; is_active?: boolean }) {
    const response = await http.post("/support/webhooks/", payload);
    return unwrapData<SupportWebhookEndpoint>(response.data);
  },

  async updateWebhook(endpointId: string, payload: Partial<{ name: string; url: string; event_types: string[]; is_active: boolean }>) {
    const response = await http.patch(`/support/webhooks/${endpointId}/`, payload);
    return unwrapData<SupportWebhookEndpoint>(response.data);
  },

  async removeWebhook(endpointId: string) {
    await http.delete(`/support/webhooks/${endpointId}/`);
  },

  async rotateWebhookSecret(endpointId: string) {
    const response = await http.post(`/support/webhooks/${endpointId}/rotate-secret/`);
    return unwrapData<{ signing_secret: string; secret_notice: string }>(response.data);
  },

  async testWebhook(endpointId: string) {
    const response = await http.post(`/support/webhooks/${endpointId}/test/`);
    return unwrapData<SupportWebhookDelivery>(response.data);
  },

  async listWebhookDeliveries(endpointId?: string, signal?: AbortSignal) {
    const response = await http.get("/support/webhooks/deliveries/", { params: endpointId ? { endpoint: endpointId } : undefined, signal });
    return unwrapData<SupportWebhookDelivery[]>(response.data);
  },

  async listDataExports(signal?: AbortSignal) {
    const response = await http.get("/support/exports/", { signal });
    return unwrapData<SupportDataExport[]>(response.data);
  },

  async createDataExport(includeAttachments?: boolean) {
    const response = await http.post("/support/exports/", includeAttachments === undefined ? {} : { include_attachments: includeAttachments });
    return unwrapData<SupportDataExport>(response.data);
  },

  async listVisitorDeletionRequests(signal?: AbortSignal) {
    const response = await http.get("/support/privacy/deletion-requests/", { signal });
    return unwrapData<SupportVisitorDeletionRequest[]>(response.data);
  },

  async requestVisitorDeletion(visitorId: string) {
    const response = await http.post(`/support/privacy/visitors/${visitorId}/delete/`);
    return unwrapData<SupportVisitorDeletionRequest>(response.data);
  },

  async getKnowledgeSettings(signal?: AbortSignal) {
    const response = await http.get("/support/knowledge/settings/", { signal });
    return unwrapData<SupportKnowledgeSettings>(response.data);
  },

  async updateKnowledgeSettings(payload: Partial<SupportKnowledgeSettings>) {
    const response = await http.patch("/support/knowledge/settings/", payload);
    return unwrapData<SupportKnowledgeSettings>(response.data);
  },

  async listKnowledgeCategories(includeInactive = false, signal?: AbortSignal) {
    const response = await http.get("/support/knowledge/categories/", {
      params: includeInactive ? { include_inactive: 1 } : undefined,
      signal,
    });
    return unwrapData<SupportKnowledgeCategory[]>(response.data);
  },

  async createKnowledgeCategory(payload: { name: string; description?: string; sort_order?: number }) {
    const response = await http.post("/support/knowledge/categories/", payload);
    return unwrapData<SupportKnowledgeCategory>(response.data);
  },

  async updateKnowledgeCategory(categoryId: string, payload: Partial<{ name: string; description: string; sort_order: number; is_active: boolean }>) {
    const response = await http.patch(`/support/knowledge/categories/${categoryId}/`, payload);
    return unwrapData<SupportKnowledgeCategory>(response.data);
  },

  async removeKnowledgeCategory(categoryId: string) {
    await http.delete(`/support/knowledge/categories/${categoryId}/`);
  },

  async listKnowledgeArticles(
    filters: { q?: string; category?: string; website?: string; status?: string } = {},
    signal?: AbortSignal,
  ) {
    const response = await http.get("/support/knowledge/articles/", { params: filters, signal });
    return unwrapData<SupportKnowledgeArticle[]>(response.data);
  },

  async createKnowledgeArticle(payload: SupportKnowledgeArticleInput) {
    const response = await http.post("/support/knowledge/articles/", payload);
    return unwrapData<SupportKnowledgeArticle>(response.data);
  },

  async updateKnowledgeArticle(articleId: string, payload: Partial<SupportKnowledgeArticleInput>) {
    const response = await http.patch(`/support/knowledge/articles/${articleId}/`, payload);
    return unwrapData<SupportKnowledgeArticle>(response.data);
  },

  async removeKnowledgeArticle(articleId: string) {
    await http.delete(`/support/knowledge/articles/${articleId}/`);
  },

  async getFeedbackSettings(signal?: AbortSignal) {
    const response = await http.get("/support/feedback-settings/", { signal });
    return unwrapData<SupportFeedbackSettings>(response.data);
  },

  async updateFeedbackSettings(payload: Partial<SupportFeedbackSettings>) {
    const response = await http.patch("/support/feedback-settings/", payload);
    return unwrapData<SupportFeedbackSettings>(response.data);
  },

  async getAnalytics(
    filters: { days?: number; start?: string; end?: string; website?: string } = {},
    signal?: AbortSignal,
  ) {
    const response = await http.get("/support/analytics/overview/", { params: filters, signal });
    return unwrapData<SupportAnalyticsOverview>(response.data);
  },

  async getConversationCSAT(conversationId: string, signal?: AbortSignal) {
    const response = await http.get(`/support/conversations/${conversationId}/csat/`, { signal });
    return unwrapData<{ settings: SupportFeedbackSettings; survey: SupportCSATSurvey | null }>(response.data);
  },

  async requestConversationCSAT(conversationId: string) {
    const response = await http.post(`/support/conversations/${conversationId}/csat/`);
    return unwrapData<SupportCSATSurvey>(response.data);
  },

  async dismissConversationCSAT(conversationId: string) {
    const response = await http.delete(`/support/conversations/${conversationId}/csat/`);
    return unwrapData<SupportCSATSurvey | null>(response.data);
  },

  async getServiceSettings(signal?: AbortSignal) {
    const response = await http.get("/support/service-settings/", { signal });
    return unwrapData<SupportServiceSettings>(response.data);
  },

  async updateServiceSettings(payload: Partial<SupportServiceSettings>) {
    const response = await http.patch("/support/service-settings/", payload);
    return unwrapData<SupportServiceSettings>(response.data);
  },

  async listServiceAlerts(status = "unread", signal?: AbortSignal) {
    const response = await http.get("/support/service-alerts/", {
      params: { status },
      signal,
    });
    return response.data as SupportServiceAlertList;
  },

  async markServiceAlertRead(alertId: string) {
    const response = await http.post(`/support/service-alerts/${alertId}/read/`);
    return unwrapData<SupportServiceAlert>(response.data);
  },

  async markAllServiceAlertsRead() {
    const response = await http.post("/support/service-alerts/read-all/");
    return unwrapData<{ updated: number }>(response.data);
  },

  async listTags(signal?: AbortSignal) {
    const response = await http.get("/support/tags/", { signal });
    return unwrapData<SupportTag[]>(response.data);
  },

  async createTag(payload: { name: string; color: string }) {
    const response = await http.post("/support/tags/", payload);
    return unwrapData<SupportTag>(response.data);
  },

  async updateTag(tagId: string, payload: Partial<{ name: string; color: string }>) {
    const response = await http.patch(`/support/tags/${tagId}/`, payload);
    return unwrapData<SupportTag>(response.data);
  },

  async removeTag(tagId: string) {
    await http.delete(`/support/tags/${tagId}/`);
  },

  async listCannedReplies(websiteId?: string, signal?: AbortSignal) {
    const response = await http.get("/support/canned-replies/", {
      params: websiteId ? { website: websiteId } : undefined,
      signal,
    });
    return unwrapData<SupportCannedReply[]>(response.data);
  },

  async createCannedReply(payload: { website_id?: string | null; shortcut: string; title: string; body: string }) {
    const response = await http.post("/support/canned-replies/", payload);
    return unwrapData<SupportCannedReply>(response.data);
  },

  async updateCannedReply(replyId: string, payload: Partial<{ website_id: string | null; shortcut: string; title: string; body: string }>) {
    const response = await http.patch(`/support/canned-replies/${replyId}/`, payload);
    return unwrapData<SupportCannedReply>(response.data);
  },

  async removeCannedReply(replyId: string) {
    await http.delete(`/support/canned-replies/${replyId}/`);
  },

  async listSavedViews(signal?: AbortSignal) {
    const response = await http.get("/support/saved-views/", { signal });
    return unwrapData<SupportSavedInboxView[]>(response.data);
  },

  async createSavedView(payload: { name: string; website_id?: string | null; queue?: string; status?: string; priority?: string; tag_id?: string | null; search?: string; is_default?: boolean }) {
    const response = await http.post("/support/saved-views/", payload);
    return unwrapData<SupportSavedInboxView>(response.data);
  },

  async updateSavedView(viewId: string, payload: Partial<{ name: string; website_id: string | null; queue: string; status: string; priority: string; tag_id: string | null; search: string; is_default: boolean }>) {
    const response = await http.patch(`/support/saved-views/${viewId}/`, payload);
    return unwrapData<SupportSavedInboxView>(response.data);
  },

  async removeSavedView(viewId: string) {
    await http.delete(`/support/saved-views/${viewId}/`);
  },

  async listConversationNotes(conversationId: string, signal?: AbortSignal) {
    const response = await http.get(`/support/conversations/${conversationId}/notes/`, { signal });
    return unwrapData<SupportInternalNote[]>(response.data);
  },

  async addConversationNote(conversationId: string, body: string) {
    const response = await http.post(`/support/conversations/${conversationId}/notes/`, { body });
    return unwrapData<SupportInternalNote>(response.data);
  },

  async updateConversationTags(conversationId: string, tagIds: string[]) {
    const response = await http.put(`/support/conversations/${conversationId}/tags/`, { tag_ids: tagIds });
    return unwrapData<SupportTag[]>(response.data);
  },

  async getConversationActivity(conversationId: string, signal?: AbortSignal) {
    const response = await http.get(`/support/conversations/${conversationId}/activity/`, { signal });
    return unwrapData<SupportConversationActivity>(response.data);
  },
  async bootstrap(signal?: AbortSignal) {
    const response = await http.get("/support/bootstrap/", { signal });
    return unwrapData<SupportBootstrap>(response.data);
  },

  async unreadSummary(signal?: AbortSignal) {
    const response = await http.get("/support/unread-summary/", { signal });
    return unwrapData<SupportUnreadSummary>(response.data);
  },

  async listConversations(
    filters: SupportConversationFilters = {},
    signal?: AbortSignal,
  ) {
    const response = await http.get("/support/conversations/", {
      params: filters,
      signal,
    });
    return response.data as SupportConversationListResponse;
  },

  async getConversationMessages(conversationId: string, signal?: AbortSignal) {
    const response = await http.get(
      `/support/conversations/${conversationId}/messages/`,
      { signal },
    );
    return unwrapData<SupportConversationMessagesResponse>(response.data);
  },

  async sendConversationMessage(
    conversationId: string,
    payload: SupportMessageSendInput,
  ) {
    const response = await http.post(
      `/support/conversations/${conversationId}/messages/`,
      payload,
    );
    return unwrapData<SupportMessage>(response.data);
  },

  async uploadConversationFile(
    conversationId: string,
    file: File,
    metadata: { durationSeconds?: number; waveform?: number[] } = {},
  ) {
    const form = new FormData();
    form.append("file", file);
    form.append("original_name", file.name);
    if (file.type) form.append("mime_type", file.type);
    if (typeof metadata.durationSeconds === "number") {
      form.append("duration_seconds", metadata.durationSeconds.toFixed(2));
    }
    if (metadata.waveform?.length)
      form.append("waveform", JSON.stringify(metadata.waveform));
    const response = await http.post(
      `/support/conversations/${conversationId}/uploads/`,
      form,
    );
    return unwrapData<SupportPendingUpload>(response.data);
  },

  async fetchMediaBlob(url: string, signal?: AbortSignal) {
    const response = await http.get<Blob>(url, {
      responseType: "blob",
      signal,
    });
    return response.data;
  },

  async updateConversation(
    conversationId: string,
    payload: SupportConversationUpdateInput,
  ) {
    const response = await http.patch(
      `/support/conversations/${conversationId}/`,
      payload,
    );
    return unwrapData<SupportConversation>(response.data);
  },

  async claimConversation(conversationId: string) {
    const response = await http.post(
      `/support/conversations/${conversationId}/claim/`,
    );
    return unwrapData<SupportConversation>(response.data);
  },

  async markConversationDelivered(conversationId: string, messageId?: string) {
    await http.post(`/support/conversations/${conversationId}/delivered/`, messageId ? { message_id: messageId } : {});
  },

  async markConversationRead(conversationId: string, messageId?: string) {
    await http.post(`/support/conversations/${conversationId}/read/`, messageId ? { message_id: messageId } : {});
  },

  async listWebsites(signal?: AbortSignal) {
    const response = await http.get("/support/websites/", { signal });
    return unwrapData<SupportWebsite[]>(response.data);
  },

  async createWebsite(payload: SupportWebsiteInput) {
    const response = await http.post("/support/websites/", payload);
    return unwrapData<SupportWebsite>(response.data);
  },

  async updateWebsite(
    websiteId: string,
    payload: Partial<SupportWebsiteInput>,
  ) {
    const response = await http.patch(
      `/support/websites/${websiteId}/`,
      payload,
    );
    return unwrapData<SupportWebsite>(response.data);
  },

  async deactivateWebsite(websiteId: string) {
    await http.delete(`/support/websites/${websiteId}/`);
  },

  async updateWidgetSettings(
    websiteId: string,
    payload: SupportWidgetSettingsInput,
  ) {
    const response = await http.patch(
      `/support/websites/${websiteId}/widget/`,
      payload,
    );
    return unwrapData<SupportWidgetSettings>(response.data);
  },

  async updateWebsiteWidgetConfiguration(
    websiteId: string,
    payload: {
      allowed_origins: string[];
      widget_enabled: boolean;
      settings: SupportWidgetSettingsInput;
    },
  ) {
    const response = await http.patch(
      `/support/websites/${websiteId}/widget-configuration/`,
      payload,
    );
    return unwrapData<SupportWebsite>(response.data);
  },

  async regenerateWebsiteSiteKey(websiteId: string) {
    const response = await http.post(
      `/support/websites/${websiteId}/site-key/regenerate/`,
    );
    return unwrapData<SupportWebsite>(response.data);
  },

  async inviteAgent(payload: SupportAgentInvitationInput) {
    const response = await http.post("/support/agents/invitations/", payload);
    return unwrapData<SupportAgentInvitation>(response.data);
  },

  async resendAgentInvitation(invitationId: string) {
    const response = await http.post(
      `/support/agents/invitations/${invitationId}/resend/`,
    );
    return unwrapData<SupportAgentInvitation>(response.data);
  },

  async revokeAgentInvitation(invitationId: string) {
    await http.delete(`/support/agents/invitations/${invitationId}/`);
  },

  async updateAgent(agentId: string, payload: SupportAgentUpdateInput) {
    const response = await http.patch(`/support/agents/${agentId}/`, payload);
    return unwrapData<SupportAgent>(response.data);
  },

  async removeAgent(agentId: string) {
    await http.delete(`/support/agents/${agentId}/`);
  },

  async updateMyAvailability(availability: SupportAvailability) {
    const response = await http.patch("/support/agents/me/availability/", {
      availability,
    });
    return unwrapData<SupportAgent>(response.data);
  },

  async previewInvitation(token: string, signal?: AbortSignal) {
    const response = await http.get("/support/invitations/preview/", {
      params: { token },
      signal,
    });
    return unwrapData<SupportInvitationPreview>(response.data);
  },

  async acceptInvitation(token: string) {
    const response = await http.post("/support/invitations/accept/", { token });
    return unwrapData<SupportAgent>(response.data);
  },
};
