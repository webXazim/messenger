import { ConversationInboxList } from "./conversations/ConversationInboxList";
import { ConversationSidebarList } from "./conversations/ConversationSidebarList";
import type { ConversationListBaseProps } from "./conversations/types";

export { conversationDisplayName, userDisplayLabel } from "./conversations/conversationPresentation";

type ConversationListProps = ConversationListBaseProps & {
  variant?: "inbox" | "sidebar";
};

export function ConversationList({ variant = "inbox", ...props }: ConversationListProps) {
  return variant === "sidebar"
    ? <ConversationSidebarList {...props} />
    : <ConversationInboxList {...props} />;
}
