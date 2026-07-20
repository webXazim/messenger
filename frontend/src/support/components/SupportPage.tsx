import type { ReactNode } from "react";
import { supportClassNames } from "../utils/classNames";

interface SupportPageProps {
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function SupportPage({ eyebrow = "Support Chat", title, description, actions, children, className }: SupportPageProps) {
  return (
    <section className={supportClassNames("sc-page", className)}>
      <header className="sc-page__header">
        <div className="sc-page__heading">{eyebrow ? <span className="sc-page__eyebrow">{eyebrow}</span> : null}<h1>{title}</h1>{description ? <p>{description}</p> : null}</div>
        {actions ? <div className="sc-page__actions">{actions}</div> : null}
      </header>
      <div className="sc-page__content">{children}</div>
    </section>
  );
}

export function SupportSurface({ children, className }: { children: ReactNode; className?: string }) {
  return <section className={supportClassNames("sc-surface", className)}>{children}</section>;
}
