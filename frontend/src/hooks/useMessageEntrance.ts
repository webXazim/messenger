import { useEffect, useRef, useState } from "react";

type MessageIdentity = {
  id: string;
  client_temp_id?: string | null;
};

export function stableMessageRenderKey(message: MessageIdentity) {
  return message.client_temp_id
    ? `client:${message.client_temp_id}`
    : `message:${message.id}`;
}

export function useMessageEntranceKeys(
  scope: string,
  messages: MessageIdentity[],
  ready: boolean,
) {
  const stateRef = useRef<{
    scope: string;
    hydrated: boolean;
    known: Set<string>;
  }>({
    scope,
    hydrated: false,
    known: new Set(),
  });
  const [entering, setEntering] = useState<Set<string>>(() => new Set());

  if (stateRef.current.scope !== scope) {
    stateRef.current = {
      scope,
      hydrated: false,
      known: new Set(),
    };
  }

  useEffect(() => {
    const state = stateRef.current;
    if (!ready || state.scope !== scope) return;
    const keys = messages.map(stableMessageRenderKey);
    if (!state.hydrated) {
      keys.forEach((key) => state.known.add(key));
      state.hydrated = true;
      setEntering(new Set());
      return;
    }

    let lastKnownIndex = -1;
    for (let index = 0; index < keys.length; index += 1) {
      if (state.known.has(keys[index])) lastKnownIndex = index;
    }
    const added = new Set(
      keys.filter((key, index) => !state.known.has(key) && index > lastKnownIndex),
    );
    keys.forEach((key) => state.known.add(key));
    if (!added.size) return;

    setEntering((current) => new Set([...current, ...added]));
    const timer = window.setTimeout(() => {
      setEntering((current) => {
        if (![...added].some((key) => current.has(key))) return current;
        const next = new Set(current);
        added.forEach((key) => next.delete(key));
        return next;
      });
    }, 220);
    return () => window.clearTimeout(timer);
  }, [messages, ready, scope]);

  return entering;
}
