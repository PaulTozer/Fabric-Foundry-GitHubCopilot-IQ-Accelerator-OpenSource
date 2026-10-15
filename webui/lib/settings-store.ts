// Per-browser persistence for the operator-level settings exposed in
// the header cog menu: agent methodology, ontology and Work IQ toggles.
//
// We deliberately keep this client-only (localStorage) rather than
// pushing it to the server — each operator likely wants their own
// preferences, and there's no auth boundary that would make a shared
// store meaningful for this app yet.

import { METHOD_OPTIONS, type SelectableMethodId } from '@/lib/types';

const STORAGE_KEY = 'enterpriseiq:settings:v1';

export interface AppSettings {
  method:             SelectableMethodId;
  ontologyEnabled:    boolean;
  /** Explicit opt-in for configured `work-iq` MCP tools on SDK turns.
   *  Does not grant licensed access or enable Work IQ for Council. */
  workIQEnabled:      boolean;
}

export const DEFAULT_SETTINGS: AppSettings = {
  method:            'M4',
  ontologyEnabled:   true,
  workIQEnabled:     false,
};

export function loadSettings(): AppSettings {
  if (typeof window === 'undefined') return { ...DEFAULT_SETTINGS };
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_SETTINGS };
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const settings: AppSettings = {
      method:            METHOD_OPTIONS.find(option => option.id === parsed.method)?.id ?? DEFAULT_SETTINGS.method,
      ontologyEnabled:   typeof parsed.ontologyEnabled === 'boolean'
        ? parsed.ontologyEnabled
        : DEFAULT_SETTINGS.ontologyEnabled,
      workIQEnabled:     typeof parsed.workIQEnabled === 'boolean'
        ? parsed.workIQEnabled
        : DEFAULT_SETTINGS.workIQEnabled,
    };
    // A legacy method choice is not consent to enable SDK Work IQ tools.
    if (parsed.method === 'M7' || Object.prototype.hasOwnProperty.call(parsed, 'ribaEnabledStages')) {
      saveSettings(settings);
    }
    return settings;
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

export function saveSettings(s: AppSettings): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch {
    // localStorage might be disabled (Safari private mode etc.) —
    // silently no-op rather than crashing the app.
  }
}
