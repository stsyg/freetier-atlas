import { useState } from "react";
import type { RecommendationRequest } from "../api";
import {
  CATEGORY_OPTIONS,
  METRIC_SUGGESTIONS,
  PERIOD_SUGGESTIONS,
  UNIT_SUGGESTIONS,
} from "./vocab";

/**
 * The editable STRUCTURED requirements form (F006 slice 4).
 *
 * This is a plain, keyboard-operable `<form>` — there is deliberately NO
 * natural-language input, NO LLM, NO consent flow, and NO export here (all of
 * that is F007). The user describes a workload as structured data:
 *
 * - an optional workload name,
 * - one or more requirements, each in a canonical category (a `<select>` of the
 *   fourteen canonical slugs), with an optional label,
 * - one or more quantified demands per requirement (metric + exact amount +
 *   explicit unit + optional period), and
 * - optional constraints (commercial use, personal use, region, residency).
 *
 * Every control has an associated `<label>`. The component never builds a URL
 * and never fetches — it validates the draft locally and emits a typed
 * {@link RecommendationRequest}; the parent owns the request. Amounts are kept
 * and emitted as strings so the backend receives the exact Decimal value.
 */

interface DemandDraft {
  key: string;
  metric: string;
  amount: string;
  unit: string;
  period: string;
}

interface RequirementDraft {
  key: string;
  category: string;
  label: string;
  demands: DemandDraft[];
  commercialUse: boolean;
  personalUseOk: boolean;
  region: string;
  residency: string;
}

let uid = 0;
function nextKey(prefix: string): string {
  uid += 1;
  return `${prefix}-${uid}`;
}

function newDemand(): DemandDraft {
  return { key: nextKey("demand"), metric: "", amount: "", unit: "", period: "" };
}

function newRequirement(): RequirementDraft {
  return {
    key: nextKey("req"),
    category: CATEGORY_OPTIONS[0].value,
    label: "",
    demands: [newDemand()],
    commercialUse: false,
    personalUseOk: true,
    region: "",
    residency: "",
  };
}

/** Trim a free-text field to `null` when it is empty (so the API omits it). */
function orNull(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

/** True when a string parses as a finite number strictly greater than zero. */
function isPositiveAmount(value: string): boolean {
  const trimmed = value.trim();
  if (trimmed === "") return false;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) && parsed > 0;
}

export interface AdviserFormProps {
  onSubmit: (request: RecommendationRequest) => void;
  disabled?: boolean;
}

export function AdviserForm({ onSubmit, disabled = false }: AdviserFormProps) {
  const [workloadName, setWorkloadName] = useState("");
  const [requirements, setRequirements] = useState<RequirementDraft[]>(() => [newRequirement()]);
  const [errors, setErrors] = useState<string[]>([]);

  const patchRequirement = (key: string, patch: Partial<RequirementDraft>) => {
    setRequirements((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  };

  const patchDemand = (reqKey: string, demandKey: string, patch: Partial<DemandDraft>) => {
    setRequirements((prev) =>
      prev.map((r) =>
        r.key === reqKey
          ? {
              ...r,
              demands: r.demands.map((d) => (d.key === demandKey ? { ...d, ...patch } : d)),
            }
          : r,
      ),
    );
  };

  const addRequirement = () => setRequirements((prev) => [...prev, newRequirement()]);
  const removeRequirement = (key: string) =>
    setRequirements((prev) => (prev.length > 1 ? prev.filter((r) => r.key !== key) : prev));

  const addDemand = (reqKey: string) =>
    setRequirements((prev) =>
      prev.map((r) => (r.key === reqKey ? { ...r, demands: [...r.demands, newDemand()] } : r)),
    );
  const removeDemand = (reqKey: string, demandKey: string) =>
    setRequirements((prev) =>
      prev.map((r) =>
        r.key === reqKey && r.demands.length > 1
          ? { ...r, demands: r.demands.filter((d) => d.key !== demandKey) }
          : r,
      ),
    );

  const validate = (): string[] => {
    const found: string[] = [];
    requirements.forEach((req, ri) => {
      const where =
        req.label.trim() ||
        CATEGORY_OPTIONS.find((c) => c.value === req.category)?.label ||
        `Requirement ${ri + 1}`;
      req.demands.forEach((demand, di) => {
        const hasAny =
          demand.metric.trim() !== "" || demand.amount.trim() !== "" || demand.unit.trim() !== "";
        // A demand row must be fully specified: metric + positive amount + unit.
        if (!hasAny) {
          found.push(`${where}: demand ${di + 1} needs a metric, amount, and unit.`);
          return;
        }
        if (demand.metric.trim() === "") {
          found.push(`${where}: demand ${di + 1} is missing a metric.`);
        }
        if (!isPositiveAmount(demand.amount)) {
          found.push(`${where}: demand ${di + 1} needs an amount greater than zero.`);
        }
        if (demand.unit.trim() === "") {
          found.push(`${where}: demand ${di + 1} is missing a unit.`);
        }
      });
    });
    return found;
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const found = validate();
    setErrors(found);
    if (found.length > 0) return;

    const request: RecommendationRequest = {
      workload_name: orNull(workloadName),
      requirements: requirements.map((req) => ({
        category: req.category,
        label: orNull(req.label),
        demands: req.demands.map((demand) => ({
          metric: demand.metric.trim(),
          amount: demand.amount.trim(),
          unit: demand.unit.trim(),
          period: orNull(demand.period),
        })),
        constraints: {
          commercial_use: req.commercialUse,
          personal_use_ok: req.personalUseOk,
          region: orNull(req.region),
          residency: orNull(req.residency),
        },
      })),
    };
    onSubmit(request);
  };

  return (
    <form className="adviser-form" onSubmit={handleSubmit} aria-label="Describe your workload">
      <div className="field field--grow">
        <label className="field__label" htmlFor="adviser-workload-name">
          Workload name (optional)
        </label>
        <input
          id="adviser-workload-name"
          type="text"
          value={workloadName}
          onChange={(e) => setWorkloadName(e.target.value)}
          placeholder="e.g. personal blog, side project API"
          maxLength={120}
        />
      </div>

      <datalist id="adviser-metric-suggestions">
        {METRIC_SUGGESTIONS.map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>
      <datalist id="adviser-unit-suggestions">
        {UNIT_SUGGESTIONS.map((u) => (
          <option key={u} value={u} />
        ))}
      </datalist>
      <datalist id="adviser-period-suggestions">
        {PERIOD_SUGGESTIONS.map((p) => (
          <option key={p} value={p} />
        ))}
      </datalist>

      <ol className="requirement-list">
        {requirements.map((req, ri) => (
          <li key={req.key} className="requirement" data-testid="requirement">
            <fieldset className="requirement__fieldset">
              <legend className="requirement__legend">Requirement {ri + 1}</legend>

              <div className="requirement__row">
                <div className="field">
                  <label className="field__label" htmlFor={`${req.key}-category`}>
                    Category
                  </label>
                  <select
                    id={`${req.key}-category`}
                    value={req.category}
                    onChange={(e) => patchRequirement(req.key, { category: e.target.value })}
                  >
                    {CATEGORY_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="field field--grow">
                  <label className="field__label" htmlFor={`${req.key}-label`}>
                    Component label (optional)
                  </label>
                  <input
                    id={`${req.key}-label`}
                    type="text"
                    value={req.label}
                    onChange={(e) => patchRequirement(req.key, { label: e.target.value })}
                    placeholder="e.g. backups, public API"
                    maxLength={120}
                  />
                </div>
              </div>

              <div className="demand-block">
                <span className="demand-block__title" id={`${req.key}-demands-title`}>
                  Demands
                </span>
                <ul className="demand-list" aria-labelledby={`${req.key}-demands-title`}>
                  {req.demands.map((demand, di) => (
                    <li key={demand.key} className="demand-row" data-testid="demand-row">
                      <div className="field">
                        <label className="field__label" htmlFor={`${demand.key}-metric`}>
                          Metric
                        </label>
                        <input
                          id={`${demand.key}-metric`}
                          type="text"
                          list="adviser-metric-suggestions"
                          value={demand.metric}
                          onChange={(e) =>
                            patchDemand(req.key, demand.key, { metric: e.target.value })
                          }
                          placeholder="e.g. storage"
                          maxLength={80}
                        />
                      </div>
                      <div className="field field--narrow">
                        <label className="field__label" htmlFor={`${demand.key}-amount`}>
                          Amount
                        </label>
                        <input
                          id={`${demand.key}-amount`}
                          type="number"
                          inputMode="decimal"
                          min="0"
                          step="any"
                          value={demand.amount}
                          onChange={(e) =>
                            patchDemand(req.key, demand.key, { amount: e.target.value })
                          }
                          placeholder="e.g. 5"
                        />
                      </div>
                      <div className="field field--narrow">
                        <label className="field__label" htmlFor={`${demand.key}-unit`}>
                          Unit
                        </label>
                        <input
                          id={`${demand.key}-unit`}
                          type="text"
                          list="adviser-unit-suggestions"
                          value={demand.unit}
                          onChange={(e) =>
                            patchDemand(req.key, demand.key, { unit: e.target.value })
                          }
                          placeholder="e.g. GB"
                          maxLength={80}
                        />
                      </div>
                      <div className="field field--narrow">
                        <label className="field__label" htmlFor={`${demand.key}-period`}>
                          Period (optional)
                        </label>
                        <input
                          id={`${demand.key}-period`}
                          type="text"
                          list="adviser-period-suggestions"
                          value={demand.period}
                          onChange={(e) =>
                            patchDemand(req.key, demand.key, { period: e.target.value })
                          }
                          placeholder="e.g. month"
                          maxLength={80}
                        />
                      </div>
                      <button
                        type="button"
                        className="button button--small"
                        onClick={() => removeDemand(req.key, demand.key)}
                        disabled={req.demands.length <= 1}
                        aria-label={`Remove demand ${di + 1} from requirement ${ri + 1}`}
                      >
                        Remove demand
                      </button>
                    </li>
                  ))}
                </ul>
                <button
                  type="button"
                  className="button button--small"
                  onClick={() => addDemand(req.key)}
                >
                  Add demand
                </button>
              </div>

              <fieldset className="constraints">
                <legend className="constraints__legend">Constraints (optional)</legend>
                <div className="constraints__row">
                  <label className="checkbox" htmlFor={`${req.key}-commercial`}>
                    <input
                      id={`${req.key}-commercial`}
                      type="checkbox"
                      checked={req.commercialUse}
                      onChange={(e) =>
                        patchRequirement(req.key, { commercialUse: e.target.checked })
                      }
                    />
                    <span>Must allow commercial use</span>
                  </label>
                  <label className="checkbox" htmlFor={`${req.key}-personal`}>
                    <input
                      id={`${req.key}-personal`}
                      type="checkbox"
                      checked={req.personalUseOk}
                      onChange={(e) =>
                        patchRequirement(req.key, { personalUseOk: e.target.checked })
                      }
                    />
                    <span>Personal use is acceptable</span>
                  </label>
                </div>
                <div className="requirement__row">
                  <div className="field">
                    <label className="field__label" htmlFor={`${req.key}-region`}>
                      Region (optional)
                    </label>
                    <input
                      id={`${req.key}-region`}
                      type="text"
                      value={req.region}
                      onChange={(e) => patchRequirement(req.key, { region: e.target.value })}
                      placeholder="e.g. us-east"
                      maxLength={40}
                    />
                  </div>
                  <div className="field">
                    <label className="field__label" htmlFor={`${req.key}-residency`}>
                      Data residency (optional)
                    </label>
                    <input
                      id={`${req.key}-residency`}
                      type="text"
                      value={req.residency}
                      onChange={(e) => patchRequirement(req.key, { residency: e.target.value })}
                      placeholder="e.g. eu"
                      maxLength={40}
                    />
                  </div>
                </div>
              </fieldset>

              <div className="requirement__actions">
                <button
                  type="button"
                  className="button button--small"
                  onClick={() => removeRequirement(req.key)}
                  disabled={requirements.length <= 1}
                  aria-label={`Remove requirement ${ri + 1}`}
                >
                  Remove requirement
                </button>
              </div>
            </fieldset>
          </li>
        ))}
      </ol>

      <div className="adviser-form__actions">
        <button type="button" className="button" onClick={addRequirement}>
          Add requirement
        </button>
        <button type="submit" className="button button--primary" disabled={disabled}>
          {disabled ? "Getting recommendation…" : "Get recommendation"}
        </button>
      </div>

      {errors.length > 0 ? (
        <div className="form-errors" role="alert" data-testid="form-errors">
          <p>Please fix the following before requesting a recommendation:</p>
          <ul>
            {errors.map((error, index) => (
              <li key={index}>{error}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </form>
  );
}
