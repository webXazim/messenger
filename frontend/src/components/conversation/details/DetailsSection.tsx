import type { ReactNode } from "react";

export function DetailsSection({
  title,
  eyebrow,
  note,
  children,
  collapsible = false,
  defaultOpen = true,
  className = "",
}: {
  title: string;
  eyebrow?: string;
  note?: ReactNode;
  children: ReactNode;
  collapsible?: boolean;
  defaultOpen?: boolean;
  className?: string;
}) {
  const heading = (
    <div className="ms-details-section__heading">
      <div>
        {eyebrow ? <span>{eyebrow}</span> : null}
        <h3>{title}</h3>
      </div>
      {note ? <div className="ms-details-section__note">{note}</div> : null}
    </div>
  );

  if (collapsible) {
    return (
      <details className={`ms-details-section ms-details-section--collapsible ${className}`.trim()} open={defaultOpen}>
        <summary>
          {heading}
          <span className="ms-details-section__chevron" aria-hidden="true">⌄</span>
        </summary>
        <div className="ms-details-section__body">{children}</div>
      </details>
    );
  }

  return (
    <section className={`ms-details-section ${className}`.trim()}>
      {heading}
      <div className="ms-details-section__body">{children}</div>
    </section>
  );
}
