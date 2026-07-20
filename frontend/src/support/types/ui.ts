import type { ReactNode } from "react";

export type SupportTone = "neutral" | "strong" | "success" | "warning" | "danger" | "info";
export type SupportSize = "sm" | "md" | "lg";

export interface SupportOption<T extends string = string> {
  label: string;
  value: T;
  disabled?: boolean;
}

export interface SupportTab<T extends string = string> {
  id: T;
  label: string;
  count?: number;
  disabled?: boolean;
}

export interface SupportTableColumn<Row> {
  id: string;
  header: ReactNode;
  cell: (row: Row) => ReactNode;
  className?: string;
  width?: string;
}
