export const TYPING_STOP_GRACE_MS = 420;
export const TYPING_MIN_VISIBLE_MS = 900;
export const TYPING_MESSAGE_TRANSITION_MS = 140;

export function typingRemovalDelay(
  visibleSince: number,
  requestedDelay: number,
  now = Date.now(),
) {
  const minimumRemaining = Math.max(0, visibleSince + TYPING_MIN_VISIBLE_MS - now);
  return Math.max(0, requestedDelay, minimumRemaining);
}
