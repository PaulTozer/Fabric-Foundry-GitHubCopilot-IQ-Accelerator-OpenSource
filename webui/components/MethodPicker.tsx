'use client';

import { METHOD_OPTIONS, type SelectableMethodId } from '@/lib/types';

interface Props {
  value:    SelectableMethodId;
  onChange: (m: SelectableMethodId) => void;
  disabled?: boolean;
}

/**
 * Compact dropdown that lets the operator switch between the agent
 * methodologies (M4 + M6). Sits in the header next to the Enterprise wordmark.
 *
 * Default selection is Copilot SDK BYOK (M4) — set by the parent.
 */
export function MethodPicker({ value, onChange, disabled }: Props) {
  const current = METHOD_OPTIONS.find((m) => m.id === value);
  return (
    <label
      className="flex items-center gap-2 text-xs text-brand-grey/70"
      title={current?.hint ?? ''}
    >
      <span className="hidden uppercase tracking-wide sm:inline">Method</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as SelectableMethodId)}
        disabled={disabled}
        className="rounded-md border border-brand-line bg-white px-2 py-1 text-sm text-brand-grey shadow-xs focus:border-brand-magenta focus:outline-hidden focus:ring-1 focus:ring-brand-magenta disabled:cursor-not-allowed disabled:opacity-60"
      >
        {METHOD_OPTIONS.map((m) => (
          <option key={m.id} value={m.id} title={m.hint}>
            {m.label}
          </option>
        ))}
      </select>
    </label>
  );
}
