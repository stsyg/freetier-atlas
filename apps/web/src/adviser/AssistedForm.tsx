import { useId, useState } from "react";
import type { AssistedRequest } from "../api";

/**
 * The natural-language ("assisted") intake form (F007 slice 1).
 *
 * The user describes their workload in plain words; on submit the parent POSTs
 * the description to `/adviser/recommend/assisted`, where a routing ladder
 * (deterministic parser -> optional, consent-gated LLM tiers -> deterministic
 * fallback) turns it into a *candidate* structured request that is validated by
 * the SAME strict schema and fed to the SAME deterministic adviser. This
 * component never builds a URL and never fetches; it emits a typed
 * {@link AssistedRequest} and the parent owns the request.
 *
 * External AI processing is strictly OPT-IN. It is off by default, so the
 * request takes the deterministic/local path. To turn it on the user must open
 * the consent dialog, read the warning (identifies the provider generically,
 * warns against secrets / personal data, explains external processing, links the
 * provider policy) and tick an explicit checkbox. Consent is EPHEMERAL: it is
 * sent only for this request, never persisted here, and re-asked each session.
 */

interface AssistedFormProps {
  onSubmit: (request: AssistedRequest) => void;
  disabled?: boolean;
  /** Client-side character bound; the server enforces the authoritative limit. */
  maxCharacters?: number;
}

/** Mirrors the conservative example config `maximum_input_characters`. */
const DEFAULT_MAX_CHARACTERS = 2000;

export function AssistedForm({
  onSubmit,
  disabled = false,
  maxCharacters = DEFAULT_MAX_CHARACTERS,
}: AssistedFormProps) {
  const [description, setDescription] = useState("");
  const [consentGranted, setConsentGranted] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const textareaId = useId();
  const counterId = useId();

  const trimmed = description.trim();
  const remaining = maxCharacters - description.length;
  const overLimit = description.length > maxCharacters;

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled) return;
    if (trimmed.length === 0) {
      setError("Please describe your workload before asking for a recommendation.");
      return;
    }
    if (overLimit) {
      setError(`Please shorten your description to ${maxCharacters} characters or fewer.`);
      return;
    }
    setError(null);
    const request: AssistedRequest = { description: trimmed };
    if (consentGranted) {
      request.consent = { external_processing: true };
    }
    onSubmit(request);
  }

  return (
    <form aria-label="Describe your workload in plain words" onSubmit={handleSubmit} noValidate>
      <div className="field">
        <label htmlFor={textareaId} className="field__label">
          Describe your workload
        </label>
        <p id={counterId} className="field__hint muted">
          Plain words only — no URLs, secrets, credentials, or personal data. For example: “A small
          API with a Postgres database and about 100,000 requests per month.”
        </p>
        <textarea
          id={textareaId}
          className="assisted__textarea"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          rows={5}
          maxLength={maxCharacters}
          aria-describedby={counterId}
          disabled={disabled}
          data-testid="assisted-description"
        />
        <p
          className={overLimit ? "assisted__counter assisted__counter--over" : "assisted__counter"}
          role="status"
          data-testid="assisted-counter"
        >
          {remaining} characters remaining
        </p>
      </div>

      <fieldset className="assisted__consent">
        <legend>External AI processing (optional)</legend>
        {consentGranted ? (
          <p className="assisted__consent-state" data-testid="consent-state">
            <span aria-hidden="true">✓ </span>
            External AI processing is enabled for your next request.{" "}
            <button
              type="button"
              className="linklike"
              onClick={() => setConsentGranted(false)}
              disabled={disabled}
            >
              Turn off
            </button>
          </p>
        ) : (
          <p className="assisted__consent-state" data-testid="consent-state">
            External AI processing is <strong>off</strong>. Your description is interpreted
            on-server with the deterministic parser and never sent to a third party.{" "}
            <button
              type="button"
              className="linklike"
              onClick={() => setModalOpen(true)}
              disabled={disabled}
            >
              Enable external AI processing…
            </button>
          </p>
        )}
      </fieldset>

      {error ? (
        <p className="status status--error" role="alert" data-testid="assisted-error">
          {error}
        </p>
      ) : null}

      <div className="adviser-form__actions">
        <button type="submit" className="button button--primary" disabled={disabled}>
          Get a recommendation
        </button>
      </div>

      {modalOpen ? (
        <ConsentModal
          onCancel={() => setModalOpen(false)}
          onConsent={() => {
            setConsentGranted(true);
            setModalOpen(false);
          }}
        />
      ) : null}
    </form>
  );
}

interface ConsentModalProps {
  onConsent: () => void;
  onCancel: () => void;
}

/**
 * A blocking, explicit opt-in dialog for external AI processing.
 *
 * Per `docs/SECURITY_PRIVACY_ABUSE.md`: it identifies the provider, warns
 * against secrets / personal or confidential data, explains what external
 * processing means, links the provider's policy, and requires an explicit
 * checkbox before the confirm button is enabled. The provider is described
 * generically because no external provider is enabled in this slice; nothing is
 * sent unless an operator enables one and the user opts in here.
 */
function ConsentModal({ onConsent, onCancel }: ConsentModalProps) {
  const [acknowledged, setAcknowledged] = useState(false);
  const titleId = useId();
  const bodyId = useId();
  const checkboxId = useId();

  return (
    <div className="modal-overlay" role="presentation" onClick={onCancel}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        onClick={(event) => event.stopPropagation()}
        data-testid="consent-modal"
      >
        <h2 id={titleId} className="modal__title">
          Enable external AI processing?
        </h2>
        <div id={bodyId} className="modal__body">
          <p>
            To interpret your description an operator may route it to the configured{" "}
            <strong>external AI provider</strong> (a third-party hosted model). This happens only if
            you opt in here, and only for this one request.
          </p>
          <p className="modal__warning">
            <strong>Do not include secrets, credentials, or personal or confidential data.</strong>{" "}
            Your description will leave this service and be processed by the third party under their
            terms.
          </p>
          <p>
            Review the provider’s policy before continuing:{" "}
            <a
              href="https://www.freetieratlas.example/llm-provider-policy"
              target="_blank"
              rel="noopener noreferrer"
            >
              external AI provider policy
            </a>
            .
          </p>
          <p className="muted">
            Your consent is not stored — it applies to this request only and is asked again next
            session. If you decline, your description is interpreted on-server with the
            deterministic parser instead.
          </p>
          <label className="modal__checkbox" htmlFor={checkboxId}>
            <input
              id={checkboxId}
              type="checkbox"
              checked={acknowledged}
              onChange={(event) => setAcknowledged(event.target.checked)}
              data-testid="consent-checkbox"
            />
            I understand and consent to external processing of this description for this request.
          </label>
        </div>
        <div className="modal__actions">
          <button type="button" className="button" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="button button--primary"
            disabled={!acknowledged}
            onClick={onConsent}
            data-testid="consent-confirm"
          >
            Enable for this request
          </button>
        </div>
      </div>
    </div>
  );
}
