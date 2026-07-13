import type { ReactNode } from "react";

export type MessengerPageStat = {
  label: string;
  value: ReactNode;
};

export function MessengerPageHeader({
  eyebrow,
  title,
  description,
  stats = [],
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  stats?: MessengerPageStat[];
  actions?: ReactNode;
}) {
  return (
    <header className="ms-page-header">
      <div className="ms-page-header__copy">
        {eyebrow ? <div className="ms-page-header__eyebrow">{eyebrow}</div> : null}
        <h1>{title}</h1>
        {description ? <p>{description}</p> : null}
      </div>
      {stats.length || actions ? (
        <div className="ms-page-header__aside">
          {stats.length ? (
            <div className="ms-page-header__stats" aria-label={`${title} summary`}>
              {stats.map((stat) => (
                <div className="ms-page-stat" key={stat.label}>
                  <strong>{stat.value}</strong>
                  <span>{stat.label}</span>
                </div>
              ))}
            </div>
          ) : null}
          {actions ? <div className="ms-page-header__actions">{actions}</div> : null}
        </div>
      ) : null}
    </header>
  );
}

export function MessengerSectionHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="ms-section-header">
      <div className="ms-section-header__copy">
        {eyebrow ? <div className="ms-section-header__eyebrow">{eyebrow}</div> : null}
        <h2>{title}</h2>
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="ms-section-header__actions">{actions}</div> : null}
    </div>
  );
}
