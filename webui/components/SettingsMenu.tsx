'use client';

import { useEffect, useRef, useState } from 'react';
import { METHOD_OPTIONS, type SelectableMethodId } from '@/lib/types';
import { ModelSettings } from './ModelSettings';
import type { OperatorConfiguration } from '@/lib/agent-config';
import { FabricWorkspaceSettings, type FabricWorkspaceSettingsProps } from './FabricWorkspaceSettings';

interface SettingsMenuProps {
  method:                  SelectableMethodId;
  onMethodChange:          (m: SelectableMethodId) => void;
  ontologyEnabled:         boolean;
  onOntologyEnabledChange: (v: boolean) => void;
  /** Explicit opt-in for configured Work IQ MCP tools on SDK turns. */
  workIQEnabled:           boolean;
  onWorkIQEnabledChange:   (v: boolean) => void;
  pending?:                boolean;
  selectedModel: string | null;
  configuration: OperatorConfiguration | null;
  onModelChange: (id: string) => void;
  onOpenAgentManager: () => void;
  fabricWorkspace: FabricWorkspaceSettingsProps;
}

/**
 * Header cog. Opens a popover with the operator controls:
 *   • Agent method (M4 / M6)
 *   • Ontology on/off (A/B the graph tool vs SQL-only for M4/M6)
 *   • Work IQ (Microsoft 365 grounding) opt-in
 * Example Enterprise themed (purple accent on the dark header). The gear
 * shows a small purple dot when any setting deviates from the defaults.
 */
export function SettingsMenu({
  method,
  onMethodChange,
  ontologyEnabled,
  onOntologyEnabledChange,
  workIQEnabled,
  onWorkIQEnabledChange,
  pending,
  selectedModel, configuration, onModelChange, onOpenAgentManager, fabricWorkspace,
}: SettingsMenuProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);

  // Close on outside click / Escape.
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') { setOpen(false); trigger.current?.focus(); }
    }
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const current = METHOD_OPTIONS.find((m) => m.id === method);

  const workIQUserToggle  = method === 'M4';
  const workIQDisplayedOn = workIQUserToggle && workIQEnabled;
  const isDefault         = method === 'M4' && ontologyEnabled && !workIQEnabled;

  return (
    <div ref={ref} className="relative">
      <button
        ref={trigger}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Settings"
        aria-expanded={open}
        title="Settings — agent method, ontology & Work IQ"
        className={`relative grid h-9 w-9 place-items-center rounded-full border border-white/20 text-white/80 transition hover:border-brand-purple hover:text-white focus:outline-hidden focus:ring-2 focus:ring-brand-purple/40 ${
          open ? 'bg-white/10' : ''
        }`}
      >
        <GearIcon />
        {!isDefault && (
          <span
            className="absolute right-1 top-1 inline-block h-1.5 w-1.5 rounded-full bg-brand-purple"
            aria-hidden="true"
          />
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-30 mt-2 w-80 overflow-hidden rounded-2xl border border-brand-line bg-white text-brand-grey shadow-card">
          <div className="flex items-center justify-between border-b border-brand-line bg-brand-paper px-4 py-3">
            <div>
              <p className="font-display text-sm font-extrabold uppercase tracking-tight text-brand-grey">
                Settings
              </p>
              <p className="text-[11px] text-brand-grey/55">
                Choose how the agent answers.
              </p>
            </div>
            <button
              type="button"
              onClick={() => { setOpen(false); trigger.current?.focus(); }}
              aria-label="Close settings"
              className="rounded-sm p-0.5 text-brand-grey/50 hover:bg-white hover:text-brand-grey"
            >
              ×
            </button>
          </div>

          <div className="max-h-[70vh] overflow-y-auto">
            <div className="px-4 pt-3">
              <button type="button" className="min-h-9 rounded-sm border border-brand-line px-3 py-1 text-sm"
                onClick={() => { setOpen(false); onOpenAgentManager(); }}>Agent Manager</button>
            </div>
            {/* Method */}
            <div className="px-4 py-3">
              <p className="mb-2 text-[11px] font-bold uppercase tracking-wider text-brand-grey/60">
                Agent method
              </p>
              <div className="space-y-2">
                {METHOD_OPTIONS.map((m) => {
                  const active = m.id === method;
                  return (
                    <button
                      key={m.id}
                      type="button"
                      disabled={pending}
                      onClick={() => onMethodChange(m.id)}
                      className={`flex w-full items-start gap-3 rounded-xl border px-3 py-2.5 text-left transition disabled:cursor-not-allowed ${
                        active
                          ? 'border-brand-purple bg-brand-purple/5'
                          : 'border-brand-line bg-white hover:border-brand-purple/50 hover:bg-brand-paper'
                      }`}
                    >
                      <span
                        className={`mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full border-2 ${
                          active ? 'border-brand-purple' : 'border-brand-line'
                        }`}
                      >
                        {active && <span className="h-2 w-2 rounded-full bg-brand-purple" />}
                      </span>
                      <span className="min-w-0">
                        <span className="flex items-center gap-2 text-[13px] font-bold text-brand-grey">
                          {m.label}
                        </span>
                        <span className="block text-[11px] leading-snug text-brand-grey/55">
                          {m.hint}
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>

            <ModelSettings selectedModel={selectedModel} configuration={configuration} onChange={onModelChange}
              disabled={Boolean(pending) || method !== 'M4'} />
            <FabricWorkspaceSettings key={fabricWorkspace.owner} {...fabricWorkspace}
              disabled={Boolean(pending) || fabricWorkspace.disabled} />

            {/* Ontology toggle */}
            <div className="border-t border-brand-line px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-[13px] font-bold text-brand-grey">Ontology</p>
                  <p className="text-[11px] leading-snug text-brand-grey/55">
                    Use the EnterpriseKG graph for relationship questions. Off = SQL only.
                  </p>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-label="Ontology"
                  aria-checked={ontologyEnabled}
                  disabled={pending}
                  onClick={() => onOntologyEnabledChange(!ontologyEnabled)}
                  className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full transition-colors focus:outline-hidden focus:ring-2 focus:ring-brand-purple/40 disabled:cursor-not-allowed disabled:opacity-50 ${
                    ontologyEnabled ? 'bg-brand-purple' : 'bg-brand-line'
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white shadow-sm transition-transform ${
                      ontologyEnabled ? 'translate-x-6' : 'translate-x-1'
                    }`}
                  />
                </button>
              </div>
            </div>

            {/* Work IQ toggle */}
            <div className="border-t border-brand-line px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-[13px] font-bold text-brand-grey">Work IQ</p>
                  <p
                    className={`text-[11px] leading-snug ${
                      workIQUserToggle ? 'text-brand-grey/55' : 'text-brand-grey/40'
                    }`}
                  >
                    {workIQUserToggle
                        ? 'Enable configured Work IQ MCP servers. Demo sources are synthetic; live Microsoft 365 requires licensed access.'
                        : 'Available for Copilot SDK turns. Council does not consult Work IQ.'}
                  </p>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-label="Work IQ"
                  aria-checked={workIQDisplayedOn}
                  disabled={pending || !workIQUserToggle}
                  onClick={() => onWorkIQEnabledChange(!workIQEnabled)}
                  className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full transition-colors focus:outline-hidden focus:ring-2 focus:ring-brand-purple/40 disabled:cursor-not-allowed disabled:opacity-50 ${
                    workIQDisplayedOn ? 'bg-brand-purple' : 'bg-brand-line'
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white shadow-sm transition-transform ${
                      workIQDisplayedOn ? 'translate-x-6' : 'translate-x-1'
                    }`}
                  />
                </button>
              </div>
            </div>

            <div className="border-t border-brand-line bg-brand-paper px-4 py-2 text-[10px] uppercase tracking-wide text-brand-grey/45">
              Current · {current?.label ?? method}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function GearIcon() {
  return (
    <svg viewBox="0 0 24 24" width={18} height={18} fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}
