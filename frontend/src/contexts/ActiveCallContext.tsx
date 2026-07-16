import { createContext, useContext, type ReactNode } from "react";

type ActiveCallSession = {
  activeCallId: string;
  activateCall: (callId: string) => void;
  expectOutgoingCall: (conversationId: string) => void;
  clearOutgoingCallExpectation: (conversationId: string) => void;
};

const ActiveCallContext = createContext<ActiveCallSession | null>(null);

export function ActiveCallProvider({
  value,
  children,
}: {
  value: ActiveCallSession;
  children: ReactNode;
}) {
  return <ActiveCallContext.Provider value={value}>{children}</ActiveCallContext.Provider>;
}

export function useActiveCall() {
  const context = useContext(ActiveCallContext);
  if (!context) throw new Error("useActiveCall must be used inside ActiveCallProvider.");
  return context;
}
