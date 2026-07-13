export type CallPoint = { x: number; y: number };

export type FloatingVideoBounds = {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
};

export function clampCallValue(value: number, minimum: number, maximum: number) {
  return Math.min(Math.max(value, minimum), Math.max(minimum, maximum));
}

export function calculateFloatingVideoBounds({
  stageWidth,
  stageHeight,
  floatingWidth,
  floatingHeight,
  edgeGap = 12,
  compactBreakpoint = 720,
  compactTopInset = 64,
  desktopTopInset = 76,
  compactBottomInset = 86,
  desktopBottomInset = 98,
}: {
  stageWidth: number;
  stageHeight: number;
  floatingWidth: number;
  floatingHeight: number;
  edgeGap?: number;
  compactBreakpoint?: number;
  compactTopInset?: number;
  desktopTopInset?: number;
  compactBottomInset?: number;
  desktopBottomInset?: number;
}): FloatingVideoBounds {
  const compact = stageWidth <= compactBreakpoint;
  const minX = Math.min(edgeGap, Math.max(0, stageWidth - floatingWidth));
  const minY = Math.min(compact ? compactTopInset : desktopTopInset, Math.max(0, stageHeight - floatingHeight));
  const maxX = Math.max(minX, stageWidth - floatingWidth - edgeGap);
  const maxY = Math.max(minY, stageHeight - floatingHeight - (compact ? compactBottomInset : desktopBottomInset));
  return { minX, minY, maxX, maxY };
}

export function positionFromRelative(relative: CallPoint, bounds: FloatingVideoBounds): CallPoint {
  return {
    x: bounds.minX + clampCallValue(relative.x, 0, 1) * (bounds.maxX - bounds.minX),
    y: bounds.minY + clampCallValue(relative.y, 0, 1) * (bounds.maxY - bounds.minY),
  };
}

export function relativeFromPosition(position: CallPoint, bounds: FloatingVideoBounds): CallPoint {
  return {
    x: bounds.maxX > bounds.minX
      ? clampCallValue((position.x - bounds.minX) / (bounds.maxX - bounds.minX), 0, 1)
      : 1,
    y: bounds.maxY > bounds.minY
      ? clampCallValue((position.y - bounds.minY) / (bounds.maxY - bounds.minY), 0, 1)
      : 0.12,
  };
}
