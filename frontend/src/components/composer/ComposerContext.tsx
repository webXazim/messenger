type ComposerContextProps = {
  mode: "reply" | "edit";
  title: string;
  meta?: string;
  preview: string;
  onDismiss: () => void;
};

function ContextIcon({ mode }: { mode: ComposerContextProps["mode"] }) {
  if (mode === "edit") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="m4 16.5-.8 4.3 4.3-.8L19 8.5 15.5 5 4 16.5Z" />
        <path d="m13.8 6.7 3.5 3.5" />
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="m9 8-5 4 5 4" />
      <path d="M5 12h8a6 6 0 0 1 6 6" />
    </svg>
  );
}

export function ComposerContext({ mode, title, meta, preview, onDismiss }: ComposerContextProps) {
  return (
    <div className={`ms-composer-context ms-composer-context--${mode}`}>
      <span className="ms-composer-context__icon"><ContextIcon mode={mode} /></span>
      <div className="ms-composer-context__copy">
        <div className="ms-composer-context__heading">
          <strong>{title}</strong>
          {meta ? <span>{meta}</span> : null}
        </div>
        <p>{preview}</p>
      </div>
      <button type="button" className="ms-composer-context__dismiss" onClick={onDismiss} aria-label={`Cancel ${mode}`}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17" /></svg>
      </button>
    </div>
  );
}
