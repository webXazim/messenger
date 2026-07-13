import {
  useEffect,
  useRef,
  useState,
  type MutableRefObject,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { VideoTile } from "./VideoTile";
import { calculateFloatingVideoBounds, clampCallValue, positionFromRelative, relativeFromPosition, type CallPoint as Point } from "./callGeometry";

type DragState = {
  pointerId: number;
  startPointer: Point;
  startPosition: Point;
  moved: boolean;
} | null;


export function FloatingLocalVideo({
  stageRef,
  videoRef,
  enabled,
  label = "You",
  fallback,
  mirrored = true,
  onActivate,
}: {
  stageRef: RefObject<HTMLDivElement | null>;
  videoRef: MutableRefObject<HTMLVideoElement | null>;
  enabled: boolean;
  label?: string;
  fallback?: ReactNode;
  mirrored?: boolean;
  onActivate?: () => void;
}) {
  const floatingRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<DragState>(null);
  const relativePositionRef = useRef<Point>({ x: 1, y: 0.12 });
  const [position, setPosition] = useState<Point>({ x: 0, y: 0 });
  const [positioned, setPositioned] = useState(false);
  const [dragging, setDragging] = useState(false);

  const measureBounds = () => {
    const stage = stageRef.current;
    const floating = floatingRef.current;
    if (!stage || !floating) return null;
    const stageRect = stage.getBoundingClientRect();
    const floatingRect = floating.getBoundingClientRect();
    return calculateFloatingVideoBounds({
      stageWidth: stageRect.width,
      stageHeight: stageRect.height,
      floatingWidth: floatingRect.width,
      floatingHeight: floatingRect.height,
    });
  };

  const placeFromRelativePosition = () => {
    const bounds = measureBounds();
    if (!bounds) return;
    const next = positionFromRelative(relativePositionRef.current, bounds);
    setPosition(next);
    setPositioned(true);
  };

  useEffect(() => {
    const stage = stageRef.current;
    const floating = floatingRef.current;
    if (!stage || !floating) return;

    const frame = window.requestAnimationFrame(placeFromRelativePosition);
    const observer = typeof ResizeObserver !== "undefined"
      ? new ResizeObserver(() => placeFromRelativePosition())
      : null;
    observer?.observe(stage);
    observer?.observe(floating);
    window.addEventListener("orientationchange", placeFromRelativePosition);
    window.addEventListener("resize", placeFromRelativePosition);

    return () => {
      window.cancelAnimationFrame(frame);
      observer?.disconnect();
      window.removeEventListener("orientationchange", placeFromRelativePosition);
      window.removeEventListener("resize", placeFromRelativePosition);
    };
  }, [stageRef]);

  const updateRelativePosition = (next: Point) => {
    const bounds = measureBounds();
    if (!bounds) return;
    relativePositionRef.current = relativeFromPosition(next, bounds);
  };

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 && event.pointerType === "mouse") return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      pointerId: event.pointerId,
      startPointer: { x: event.clientX, y: event.clientY },
      startPosition: position,
      moved: false,
    };
    setDragging(true);
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const bounds = measureBounds();
    if (!bounds) return;
    const deltaX = event.clientX - drag.startPointer.x;
    const deltaY = event.clientY - drag.startPointer.y;
    if (Math.abs(deltaX) > 4 || Math.abs(deltaY) > 4) drag.moved = true;
    const next = {
      x: clampCallValue(drag.startPosition.x + deltaX, bounds.minX, bounds.maxX),
      y: clampCallValue(drag.startPosition.y + deltaY, bounds.minY, bounds.maxY),
    };
    setPosition(next);
    updateRelativePosition(next);
  };

  const finishPointer = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const shouldActivate = !drag.moved;
    dragRef.current = null;
    setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    if (shouldActivate) onActivate?.();
  };

  return (
    <div
      ref={floatingRef}
      className={`ms-video-call__floating-local ${dragging ? "is-dragging" : ""} ${positioned ? "is-positioned" : ""}`}
      style={{ transform: `translate3d(${position.x}px, ${position.y}px, 0)` }}
      role="button"
      tabIndex={0}
      aria-label={`${label} floating video. Drag to move or tap to switch.`}
      onKeyDown={(event) => {
        if (!["Enter", " "].includes(event.key)) return;
        event.preventDefault();
        onActivate?.();
      }}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={finishPointer}
      onPointerCancel={finishPointer}
    >
      <VideoTile
        label={label}
        className={`${mirrored ? "ms-video-tile--local" : "ms-video-tile--floating-remote"}`}
        videoRef={videoRef}
        muted
        showVideo={enabled}
        fallback={fallback}
      />
      <span className="ms-video-call__floating-hint" aria-hidden="true">Drag · tap to switch</span>
    </div>
  );
}
