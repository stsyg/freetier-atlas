import { useState } from "react";
import type { ProviderSummary, SearchQuery } from "../api";
import type { CategoryMatrixRow } from "../api";
import {
  COMMERCIAL_USE_OPTIONS,
  OFFER_TYPE_OPTIONS,
  STATUS_OPTIONS,
  EVIDENCE_CURRENCY_OPTIONS,
  ZERO_COST_CLASS_OPTIONS,
  type Option,
} from "./vocab";

/**
 * The search box + composable filter controls for the catalogue browser.
 *
 * Rendered as a native, keyboard-operable `<form>`: a labelled text input for
 * the keyword and labelled `<select>`s for every filter (provider, category,
 * zero-cost class, offer type, commercial use, status). Submitting (or pressing
 * Enter) emits a typed {@link SearchQuery}; the component never builds a URL and
 * never fetches — the parent owns the request. Provider and category options are
 * supplied by the parent (derived from the API), never hard-coded.
 */
export interface SearchControlsProps {
  value: SearchQuery;
  providers: ProviderSummary[];
  categories: CategoryMatrixRow[];
  onSubmit: (query: SearchQuery) => void;
  onReset: () => void;
}

function toNull(value: string): string | null {
  return value === "" ? null : value;
}

export function SearchControls({
  value,
  providers,
  categories,
  onSubmit,
  onReset,
}: SearchControlsProps) {
  const [q, setQ] = useState(value.q ?? "");
  const [provider, setProvider] = useState(value.provider ?? "");
  const [category, setCategory] = useState(value.category ?? "");
  const [zeroCostClass, setZeroCostClass] = useState(value.zero_cost_class ?? "");
  const [offerType, setOfferType] = useState(value.offer_type ?? "");
  const [commercialUse, setCommercialUse] = useState(
    value.commercial_use === true ? "true" : value.commercial_use === false ? "false" : "",
  );
  const [status, setStatus] = useState(value.status ?? "");
  const [evidenceCurrent, setEvidenceCurrent] = useState(
    value.evidence_current === true ? "true" : value.evidence_current === false ? "false" : "",
  );

  const providerOptions: Option[] = providers.map((p) => ({ value: p.slug, label: p.name }));
  const categoryOptions: Option[] = categories.map((c) => ({ value: c.slug, label: c.name }));

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    onSubmit({
      q: toNull(q.trim()),
      provider: toNull(provider),
      category: toNull(category),
      zero_cost_class: toNull(zeroCostClass),
      offer_type: toNull(offerType),
      commercial_use: commercialUse === "" ? null : commercialUse === "true",
      status: toNull(status),
      evidence_current: evidenceCurrent === "" ? null : evidenceCurrent === "true",
      page: 1,
    });
  };

  const handleReset = () => {
    setQ("");
    setProvider("");
    setCategory("");
    setZeroCostClass("");
    setOfferType("");
    setCommercialUse("");
    setStatus("");
    setEvidenceCurrent("");
    onReset();
  };

  return (
    <form className="search-controls" onSubmit={handleSubmit} aria-label="Search and filter offers">
      <div className="search-controls__row">
        <label className="field field--grow" htmlFor="search-q">
          <span className="field__label">Keyword</span>
          <input
            id="search-q"
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="e.g. workers, storage, database"
            maxLength={128}
          />
        </label>
      </div>

      <div className="search-controls__row search-controls__filters">
        <Select
          id="filter-provider"
          label="Provider"
          value={provider}
          onChange={setProvider}
          options={providerOptions}
          anyLabel="Any provider"
        />
        <Select
          id="filter-category"
          label="Category"
          value={category}
          onChange={setCategory}
          options={categoryOptions}
          anyLabel="Any category"
        />
        <Select
          id="filter-zero-cost-class"
          label="Zero-cost class"
          value={zeroCostClass}
          onChange={setZeroCostClass}
          options={ZERO_COST_CLASS_OPTIONS}
          anyLabel="Any class"
        />
        {/* A SEPARATE axis from the class above. The class describes the offer's
            terms; this describes the state of the evidence behind them. They are
            deliberately not merged: a "show me free things" filter that silently
            dropped offers with expired evidence would hide real free offers, and
            an omission is invisible to the reader in a way a label is not. */}
        <Select
          id="filter-evidence-current"
          label="Evidence"
          value={evidenceCurrent}
          onChange={setEvidenceCurrent}
          options={EVIDENCE_CURRENCY_OPTIONS}
          anyLabel="Any evidence state"
        />
        <Select
          id="filter-offer-type"
          label="Offer type"
          value={offerType}
          onChange={setOfferType}
          options={OFFER_TYPE_OPTIONS}
          anyLabel="Any type"
        />
        <Select
          id="filter-commercial-use"
          label="Commercial use"
          value={commercialUse}
          onChange={setCommercialUse}
          options={COMMERCIAL_USE_OPTIONS}
          anyLabel="Any"
        />
        <Select
          id="filter-status"
          label="Status"
          value={status}
          onChange={setStatus}
          options={STATUS_OPTIONS}
          anyLabel="Any status"
        />
      </div>

      <div className="search-controls__actions">
        <button type="submit" className="button button--primary">
          Search
        </button>
        <button type="button" className="button" onClick={handleReset}>
          Clear filters
        </button>
      </div>
    </form>
  );
}

function Select({
  id,
  label,
  value,
  onChange,
  options,
  anyLabel,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: Option[];
  anyLabel: string;
}) {
  return (
    <label className="field" htmlFor={id}>
      <span className="field__label">{label}</span>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{anyLabel}</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}
