import { useId } from 'react';

/** One shared input/button styling + labelled-field primitives (F-m6,
 * F-m5). Replaces the three separate `inputClass`/`inputCls` definitions
 * (CeoDashboardPage, HeadOfficePage, CeoOpsSection) and gives every field
 * an associated label: `Field` generates a stable id and wires `htmlFor`
 * (previously zero `htmlFor` in the whole frontend). The `dark:` variants
 * come from the Head Office definition, which was the only one that had
 * them — CEO pages never get the `dark` class, so they are inert there. */

export const inputClass =
  'w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-800 dark:text-slate-100 placeholder:text-slate-400 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent';

export const labelClass = 'block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5';

export const primaryButtonClass =
  'text-xs font-semibold bg-brand-600 hover:bg-brand-700 text-white px-4 py-2 rounded-lg transition-colors disabled:opacity-50';

/** Label + control pair. Pass `id` to fix it, otherwise one is generated.
 * `optional` renders the "(optional)" hint the report forms use. */
export function Field({ label, optional, id, children, className = '' }) {
  const generatedId = useId();
  const fieldId = id || `field-${generatedId.replace(/:/g, '')}`;
  return (
    <div className={className}>
      {label && (
        <label htmlFor={fieldId} className={labelClass}>
          {label} {optional && <span className="text-slate-400 font-normal">(optional)</span>}
        </label>
      )}
      {children(fieldId)}
    </div>
  );
}

export function TextInput({ label, optional, id, className = '', ...props }) {
  return (
    <Field label={label} optional={optional} id={id}>
      {(fieldId) => <input id={fieldId} className={`${inputClass} ${className}`} {...props} />}
    </Field>
  );
}

export function TextArea({ label, optional, id, className = '', ...props }) {
  return (
    <Field label={label} optional={optional} id={id}>
      {(fieldId) => <textarea id={fieldId} className={`${inputClass} ${className}`} {...props} />}
    </Field>
  );
}

/** One-tap chip selector for enum fields (stress, energy/mood): tap to
 * set, tap the active chip again to clear. Moved verbatim from
 * CeoDashboardPage so Head Office (or any future form) can reuse it. */
export function ChipGroup({ label, options, value, onChange }) {
  return (
    <div>
      <span className={labelClass} id={`chip-${label.replace(/\W+/g, '-').toLowerCase()}`}>
        {label} <span className="text-slate-400 font-normal">(optional)</span>
      </span>
      <div className="flex gap-2 flex-wrap" role="group" aria-labelledby={`chip-${label.replace(/\W+/g, '-').toLowerCase()}`}>
        {options.map((opt) => {
          const selected = value === opt;
          return (
            <button
              key={opt}
              type="button"
              aria-pressed={selected}
              onClick={() => onChange(selected ? '' : opt)}
              className={`px-3.5 py-2 rounded-full text-xs font-semibold border transition-colors ${
                selected
                  ? 'bg-brand-600 border-brand-600 text-white'
                  : 'bg-white border-slate-200 text-slate-600 hover:border-brand-300 hover:text-brand-700'
              }`}
            >
              {opt}
            </button>
          );
        })}
      </div>
    </div>
  );
}
