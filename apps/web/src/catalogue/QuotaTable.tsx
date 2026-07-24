import type { Quota } from "../api";
import { humanizeToken, orUnknown } from "./format";

/**
 * The offer's quota rows (scope item 7): amount, unit, reset period, scope, and
 * exhaustion behaviour, rendered as an accessible table with column headers.
 * Absent values render as an honest "Unknown".
 */
export function QuotaTable({ quotas }: { quotas: Quota[] }) {
  if (quotas.length === 0) {
    return <p className="muted">No quota limits are recorded for this offer.</p>;
  }

  return (
    <table className="quota-table" data-testid="quota-table">
      <caption className="sr-only">Quota limits for this offer</caption>
      <thead>
        <tr>
          <th scope="col">Metric</th>
          <th scope="col">Amount</th>
          <th scope="col">Unit</th>
          <th scope="col">Resets</th>
          <th scope="col">Scope</th>
          <th scope="col">When exhausted</th>
        </tr>
      </thead>
      <tbody>
        {quotas.map((quota, index) => (
          <tr key={`${quota.metric}-${index}`}>
            <th scope="row">{humanizeToken(quota.metric)}</th>
            <td>{orUnknown(quota.amount)}</td>
            <td>{orUnknown(quota.unit)}</td>
            <td>{humanizeToken(quota.reset_period)}</td>
            <td>{humanizeToken(quota.scope)}</td>
            <td>{humanizeToken(quota.exhaustion_behaviour)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
