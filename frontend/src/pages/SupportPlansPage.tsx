import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { supportApi } from "../api/support";
import { parseApiError } from "../lib/apiErrors";

type BillingCycle = "monthly" | "annual";

type SupportPlan = {
  code: string;
  name: string;
  description: string;
  monthlyPrice: number;
  annualPrice: number;
  websites: string;
  agents: string;
  response: string;
  featured?: boolean;
};

const PLANS: SupportPlan[] = [
  {
    code: "starter",
    name: "Starter",
    description: "For a small team adding dependable website support.",
    monthlyPrice: 19,
    annualPrice: 190,
    websites: "1 website",
    agents: "3 agents",
    response: "Standard support",
  },
  {
    code: "growth",
    name: "Growth",
    description: "For growing teams handling support across several sites.",
    monthlyPrice: 59,
    annualPrice: 590,
    websites: "5 websites",
    agents: "15 agents",
    response: "Priority support",
    featured: true,
  },
  {
    code: "scale",
    name: "Scale",
    description: "For established operations that need room and oversight.",
    monthlyPrice: 149,
    annualPrice: 1490,
    websites: "20 websites",
    agents: "50 agents",
    response: "Priority onboarding",
  },
];

const INCLUDED_FEATURES = [
  ["Shared support inbox", "Keep visitor conversations separate from personal Messenger."],
  ["Custom website widget", "Match your brand, collect visitor details, and allow attachments."],
  ["Realtime team workflow", "Assign conversations, add private notes, tags, saved views, and canned replies."],
  ["Knowledge and feedback", "Publish answers in the widget and measure customer satisfaction."],
  ["Analytics and service targets", "Track response times, workload, follow-ups, and overdue conversations."],
  ["Privacy and integrations", "Use signed webhooks, controlled exports, and visitor deletion tools."],
];

export function SupportPlansPage() {
  const [cycle, setCycle] = useState<BillingCycle>("monthly");
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const annualSavings = useMemo(
    () => PLANS.map((plan) => plan.monthlyPrice * 12 - plan.annualPrice),
    [],
  );
  const activate = useMutation({
    mutationFn: (planCode: string) => supportApi.activatePlan(planCode),
    onMutate: () => setError(null),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["support-bootstrap"] });
      navigate("/support/websites", { replace: true });
    },
    onError: (reason) => setError(parseApiError(reason, "The Support Chat trial could not be activated.").message),
  });

  return (
    <div className="ms-support-plans-page">
      <header className="ms-support-plans-hero">
        <div className="ms-support-plans-hero__copy">
          <div className="ms-support-access-state__eyebrow">Support Chat plans</div>
          <h1>Professional website support, without enterprise complexity.</h1>
          <p>
            Every plan includes the complete Support Chat toolkit. Choose based on the number of
            websites and teammates you need.
          </p>
        </div>
        <div className="ms-support-billing-toggle" role="group" aria-label="Billing cycle">
          <button type="button" className={cycle === "monthly" ? "is-active" : ""} onClick={() => setCycle("monthly")}>
            Monthly
          </button>
          <button type="button" className={cycle === "annual" ? "is-active" : ""} onClick={() => setCycle("annual")}>
            Annual <span>2 months free</span>
          </button>
        </div>
      </header>

      <section className="ms-support-plan-grid" aria-label="Available Support Chat plans">
        {PLANS.map((plan, index) => {
          const displayPrice = cycle === "monthly" ? plan.monthlyPrice : Math.floor(plan.annualPrice / 12);
          return (
            <article className={`ms-support-plan-card${plan.featured ? " is-featured" : ""}`} key={plan.code}>
              {plan.featured ? <div className="ms-support-plan-card__badge">Most popular</div> : null}
              <div className="ms-support-plan-card__heading">
                <h2>{plan.name}</h2>
                <p>{plan.description}</p>
              </div>
              <div className="ms-support-plan-price">
                <span>$</span><strong>{displayPrice}</strong><small>/month</small>
              </div>
              <div className="ms-support-plan-billing">
                {cycle === "annual"
                  ? `Billed $${plan.annualPrice} yearly · save $${annualSavings[index]}`
                  : "Billed monthly · cancel before the next renewal"}
              </div>
              <ul className="ms-support-plan-limits">
                <li><span aria-hidden="true">✓</span><strong>{plan.websites}</strong></li>
                <li><span aria-hidden="true">✓</span><strong>{plan.agents}</strong></li>
                <li><span aria-hidden="true">✓</span>{plan.response}</li>
                <li><span aria-hidden="true">✓</span>All Support Chat features</li>
              </ul>
              <button
                className={`ms-button ${plan.featured ? "ms-button--primary" : "ms-button--ghost"}`}
                type="button"
                disabled={activate.isPending}
                onClick={() => activate.mutate(plan.code)}
              >
                {activate.isPending && activate.variables === plan.code ? "Activating…" : "Start 14-day trial"}
              </button>
            </article>
          );
        })}
      </section>
      {error ? <div className="ms-page-error ms-support-plan-error" role="alert">{error}</div> : null}

      <section className="ms-support-plan-included">
        <div className="ms-support-plan-included__heading">
          <div>
            <div className="ms-support-access-state__eyebrow">Included in every plan</div>
            <h2>Everything your support team needs</h2>
          </div>
          <p>No feature maze or paid add-ons. Limits only change as your team grows.</p>
        </div>
        <div className="ms-support-plan-feature-grid">
          {INCLUDED_FEATURES.map(([title, description]) => (
            <article key={title}>
              <span aria-hidden="true">✓</span>
              <div><strong>{title}</strong><p>{description}</p></div>
            </article>
          ))}
        </div>
      </section>

      <section className="ms-support-plan-notice">
        <div>
          <strong>Try every Support Chat feature for 14 days.</strong>
          <p>No payment is collected during this temporary testing flow. Choose a tier, then configure your first website.</p>
        </div>
        <div className="ms-support-plan-notice__actions">
          <a className="ms-button ms-button--primary" href="mailto:support@crescentsphere.com?subject=Support%20Chat%20plan%20question">Talk to us</a>
          <Link className="ms-button ms-button--ghost" to="/support/inbox">Back to Support Chat</Link>
        </div>
      </section>
    </div>
  );
}
