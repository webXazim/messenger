import { useId, type ReactNode, type SelectHTMLAttributes } from "react";
import type { SupportOption } from "../types/ui";
import { supportClassNames } from "../utils/classNames";

interface SupportSelectProps<T extends string> extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "children"> {
  label?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  options: Array<SupportOption<T>>;
}

export function SupportSelect<T extends string>({ label, hint, error, options, className, id, ...props }: SupportSelectProps<T>) {
  const generatedId = useId();
  const selectId = id || generatedId;
  const content = (
    <span className="sc-select-wrap">
      <select id={selectId} className={supportClassNames("sc-select", className)} aria-invalid={Boolean(error)} {...props}>
        {options.map((option) => <option key={option.value} value={option.value} disabled={option.disabled}>{option.label}</option>)}
      </select>
      <span aria-hidden="true">⌄</span>
    </span>
  );
  if (!label) return content;
  return <label className={supportClassNames("sc-field", Boolean(error) && "sc-field--error")} htmlFor={selectId}><span className="sc-field__label">{label}</span>{content}{(error || hint) ? <span className="sc-field__message">{error || hint}</span> : null}</label>;
}
