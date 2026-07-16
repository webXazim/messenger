type ActiveConversationViewState = {
  conversationId: string;
  atLatest: boolean;
  visible: boolean;
};

let activeConversationView: ActiveConversationViewState = {
  conversationId: "",
  atLatest: false,
  visible: false,
};

export function setActiveConversationView(state: ActiveConversationViewState) {
  activeConversationView = state;
}

export function clearActiveConversationView(conversationId: string) {
  if (activeConversationView.conversationId !== conversationId) return;
  activeConversationView = { conversationId: "", atLatest: false, visible: false };
}

export function isConversationActivelyViewedAtLatest(conversationId: string) {
  if (!conversationId || activeConversationView.conversationId !== conversationId) return false;
  if (!activeConversationView.atLatest || !activeConversationView.visible) return false;
  return typeof document === "undefined" || document.visibilityState === "visible";
}

export function applyActiveConversationReadState<T extends { id: string; unread_count: number }>(conversation: T): T {
  if (!conversation.unread_count || !isConversationActivelyViewedAtLatest(conversation.id)) return conversation;
  return { ...conversation, unread_count: 0 };
}
