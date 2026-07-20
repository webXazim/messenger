import type { Key, ReactNode } from "react";
import type { SupportTableColumn } from "../types/ui";
import { supportClassNames } from "../utils/classNames";
import { SupportState } from "./SupportState";

interface SupportDataTableProps<Row> {
  columns: Array<SupportTableColumn<Row>>;
  rows: Row[];
  rowKey: (row: Row) => Key;
  selectedKey?: Key | null;
  onRowClick?: (row: Row) => void;
  emptyTitle?: ReactNode;
  emptyDescription?: ReactNode;
  isLoading?: boolean;
  loadingLabel?: ReactNode;
  caption?: string;
}

export function SupportDataTable<Row>({ columns, rows, rowKey, selectedKey, onRowClick, emptyTitle = "No records found", emptyDescription, isLoading, loadingLabel = "Loading support data", caption }: SupportDataTableProps<Row>) {
  if (isLoading) return <SupportState kind="loading" title={loadingLabel} />;
  if (!rows.length) return <SupportState title={emptyTitle} description={emptyDescription} />;
  return (
    <div className="sc-table-wrap">
      <table className="sc-table">
        {caption ? <caption className="sc-visually-hidden">{caption}</caption> : null}
        <thead><tr>{columns.map((column) => <th key={column.id} className={column.className} style={column.width ? { width: column.width } : undefined}>{column.header}</th>)}</tr></thead>
        <tbody>{rows.map((row) => {
          const key = rowKey(row);
          return <tr key={key} className={supportClassNames(selectedKey === key && "is-selected", onRowClick && "is-interactive")} tabIndex={onRowClick ? 0 : undefined} onClick={() => onRowClick?.(row)} onKeyDown={(event) => { if (onRowClick && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); onRowClick(row); } }}>{columns.map((column) => <td key={column.id} className={column.className}>{column.cell(row)}</td>)}</tr>;
        })}</tbody>
      </table>
    </div>
  );
}
