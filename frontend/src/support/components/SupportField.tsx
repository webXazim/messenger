import { forwardRef, useId, type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes } from "react";
import { supportClassNames } from "../utils/classNames";

interface SharedFieldProps {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  optional?: boolean;
  className?: string;
}

export const SupportField = forwardRef<HTMLInputElement, SharedFieldProps & InputHTMLAttributes<HTMLInputElement>>(
  function SupportField({ label, hint, error, optional, className, id, ...props }, ref) {
    const generatedId = useId();
    const inputId = id || generatedId;
    const descriptionId = `${inputId}-description`;
    return (
      <label className={supportClassNames("sc-field", Boolean(error) && "sc-field--error", className)} htmlFor={inputId}>
        <span className="sc-field__label">{label}{optional ? <small>Optional</small> : null}</span>
        <input ref={ref} id={inputId} className="sc-input" aria-invalid={Boolean(error)} aria-describedby={(hint || error) ? descriptionId : undefined} {...props} />
        {(hint || error) ? <span id={descriptionId} className="sc-field__message">{error || hint}</span> : null}
      </label>
    );
  },
);

export const SupportTextarea = forwardRef<HTMLTextAreaElement, SharedFieldProps & TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function SupportTextarea({ label, hint, error, optional, className, id, ...props }, ref) {
    const generatedId = useId();
    const inputId = id || generatedId;
    const descriptionId = `${inputId}-description`;
    return (
      <label className={supportClassNames("sc-field", Boolean(error) && "sc-field--error", className)} htmlFor={inputId}>
        <span className="sc-field__label">{label}{optional ? <small>Optional</small> : null}</span>
        <textarea ref={ref} id={inputId} className="sc-textarea" aria-invalid={Boolean(error)} aria-describedby={(hint || error) ? descriptionId : undefined} {...props} />
        {(hint || error) ? <span id={descriptionId} className="sc-field__message">{error || hint}</span> : null}
      </label>
    );
  },
);
