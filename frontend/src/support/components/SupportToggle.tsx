import { useId, type ReactNode } from "react";

interface SupportToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
}

export function SupportToggle({ checked, onChange, label, description, disabled }: SupportToggleProps) {
  const id = useId();
  return (
    <label className="sc-toggle-row" htmlFor={id}>
      <span><strong>{label}</strong>{description ? <small>{description}</small> : null}</span>
      <span className="sc-toggle">
        <input id={id} type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} disabled={disabled} />
        <span aria-hidden="true" />
      </span>
    </label>
  );
}
