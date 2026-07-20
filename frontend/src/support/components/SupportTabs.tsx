import type { SupportTab } from "../types/ui";
import { supportClassNames } from "../utils/classNames";

interface SupportTabsProps<T extends string> {
  tabs: Array<SupportTab<T>>;
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string;
}

export function SupportTabs<T extends string>({ tabs, value, onChange, ariaLabel }: SupportTabsProps<T>) {
  return (
    <div className="sc-tabs" role="tablist" aria-label={ariaLabel}>
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          className={supportClassNames("sc-tabs__item", value === tab.id && "is-active")}
          aria-selected={value === tab.id}
          disabled={tab.disabled}
          onClick={() => onChange(tab.id)}
        >
          <span>{tab.label}</span>{typeof tab.count === "number" ? <small>{tab.count}</small> : null}
        </button>
      ))}
    </div>
  );
}
