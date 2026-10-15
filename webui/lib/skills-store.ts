import type { Skill } from './types';
import { SEED_SKILLS } from './skills-seed';

/**
 * Tiny client-side skills store. Persists to localStorage so the user's
 * library survives reloads but stays per-browser (we don't have a
 * server-side per-user store yet, and the skills are non-sensitive
 * prompt templates).
 *
 * On first load we seed the library from `skills-seed.ts`. On subsequent
 * loads user-created entries remain authoritative, but built-in `seed-*`
 * entries are refreshed from the shipped definitions. This lets prompt fixes
 * reach existing browsers without deleting a user's custom skills.
 */

const STORAGE_KEY = 'enterpriseiq:skills:v1';

function isBrowser(): boolean {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';
}

/**
 * Read the user's saved skills. Returns the freshly-seeded library when
 * nothing is in storage yet (or the stored payload is corrupt).
 */
export function loadSkills(): Skill[] {
  if (!isBrowser()) return seedClone();
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return seedClone();
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return seedClone();
    // Filter to entries that vaguely look like a Skill — defensive
    // against schema drift from earlier prototypes.
    const ok = parsed.filter(isSkillShape) as Skill[];
    return ok.length > 0 ? mergeBuiltins(ok) : seedClone();
  } catch {
    return seedClone();
  }
}

/** Persist the entire library. No-op on the server. */
export function saveSkills(skills: Skill[]): void {
  if (!isBrowser()) return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(skills));
  } catch {
    // Quota or privacy mode — silently drop; the in-memory copy is
    // still authoritative for this session.
  }
}

/** Returns a mutable copy of the seed library with timestamps set to now,
 *  so the "Created" column shows something sensible on first load. */
function seedClone(): Skill[] {
  const now = Date.now();
  return SEED_SKILLS.map((s) => ({ ...s, createdAt: now, updatedAt: now }));
}

/** Replace stored built-ins with the current shipped definitions while
 * preserving custom/user skills and the original built-in creation date. */
function mergeBuiltins(stored: Skill[]): Skill[] {
  const now = Date.now();
  const seedIds = new Set(SEED_SKILLS.map((skill) => skill.id));
  const storedById = new Map(stored.map((skill) => [skill.id, skill]));
  const builtins = SEED_SKILLS.map((seed) => {
    const previous = storedById.get(seed.id);
    return {
      ...seed,
      createdAt: previous?.createdAt ?? now,
      updatedAt: now,
    };
  });
  const custom = stored.filter((skill) => !seedIds.has(skill.id));
  return [...builtins, ...custom];
}

function isSkillShape(x: unknown): boolean {
  if (!x || typeof x !== 'object') return false;
  const o = x as Record<string, unknown>;
  return typeof o.id === 'string'
    && typeof o.name === 'string'
    && typeof o.prompt === 'string'
    && Array.isArray(o.businessAreas)
    && Array.isArray(o.regions);
}

/** Filter helper used by the sidebar. All three filters are AND-combined,
 *  the two tag axes are OR-within-axis (i.e. "any selected region"). */
export function filterSkills(
  skills: Skill[],
  query: string,
  businessAreas: string[],
  regions: string[],
): Skill[] {
  const q = query.trim().toLowerCase();
  return skills.filter((s) => {
    if (q) {
      const hay = (s.name + ' ' + s.description + ' ' + s.prompt
        + ' ' + s.businessAreas.join(' ') + ' ' + s.regions.join(' ')).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (businessAreas.length > 0
        && !s.businessAreas.some((b) => businessAreas.includes(b))) return false;
    if (regions.length > 0
        && !s.regions.some((r) => regions.includes(r))) return false;
    return true;
  });
}
