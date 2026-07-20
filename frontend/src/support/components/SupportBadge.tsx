import type { HTMLAttributes, ReactNode } from "react";
import type { SupportTone } from "../types/ui";
import { supportClassNames } from "../utils/classNames";

interface SupportBadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: SupportTone;
  dot?: boolean;
  children: ReactNode;
}

export function SupportBadge({ tone = "neutral", dot = false, children, className, ...props }: SupportBadgeProps) {
  return <span className={supportClassNames("sc-badge", `sc-badge--${tone}`, className)} {...props}>{dot ? <i aria-hidden="true" /> : null}{children}</span>;
}
