'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { DocumentLink } from '@/components/DocumentLink';
import { safeMarkdownUrl } from '@/lib/document-policy';
import { progressDetail, progressHeadline, progressRequestId } from '@/lib/query-progress';
import type { AttachmentSummary, Message, MethodId, ProgressEvent, SkillRef } from '@/lib/types';

interface MessageListProps {
  messages:     Message[];
  pending:      boolean;
  progress?:    ProgressEvent[];
  method?:      MethodId;
  onPickSample?: (text: string) => void;
}

export function MessageList({ messages, pending, progress, method, onPickSample }: MessageListProps) {
  const endRef = useRef<HTMLDivElement | null>(null);

  // Auto-scroll to the latest message — but only once a conversation has
  // started. In the empty state we must stay at the top so the hero shows.
  useEffect(() => {
    if (messages.length === 0 && !pending) return;
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, pending, progress]);

  if (messages.length === 0 && !pending) {
    return <EmptyState onPick={onPickSample} />;
  }

  return (
    <div className="mx-auto w-full max-w-5xl space-y-4 px-4 py-6">
      {messages.map((m) => (
        <Bubble key={m.id} message={m} />
      ))}
      {pending && <PendingBubble events={progress ?? []} method={method} />}
      <div ref={endRef} />
    </div>
  );
}

// Custom markdown renderers — wrap tables in a horizontal-scroll container so
// wide result sets don't push past the white assistant bubble, and constrain
// embedded images (e.g. simulate_crowd_flow data-URIs) so they don't blow out
// the layout.
const mdComponents: Components = {
  a: ({ href, children }) => <DocumentLink href={href ?? ''}>{children}</DocumentLink>,
  table: ({ node: _node, ...props }) => (
    <div className="my-3 -mx-1 overflow-x-auto">
      <table {...props} />
    </div>
  ),
  img: ({ node: _node, alt, ...props }) => (
    <img
      alt={alt ?? ''}
      {...props}
      className="my-3 max-h-[420px] w-auto max-w-full rounded-sm border border-brand-line"
    />
  ),
};

function Bubble({ message }: { message: Message }) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="flex max-w-[85%] flex-col items-end gap-1.5">
          {message.skill && <SkillChip skill={message.skill} />}
          {message.attachments && message.attachments.length > 0 && (
            <ul className="flex flex-wrap justify-end gap-1.5" aria-label="Attached files">
              {message.attachments.map((a) => (
                <li key={a.id}>
                  <AttachmentTag att={a} />
                </li>
              ))}
            </ul>
          )}
          {message.content && (
            <div className="chat-bubble-user whitespace-pre-wrap">
              {message.content}
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="group/msg flex justify-start">
      <div className="chat-bubble-asst min-w-0 w-full max-w-full">
        <div className="mb-1 flex flex-wrap items-center gap-1.5">
          {message.method && <MethodBadge method={message.method} />}
          {message.model && <span className="text-xs">Model: {message.model}</span>}
          {message.grounding === 'ungrounded' && <span className="text-xs font-semibold">Ungrounded model response</span>}
        </div>
        {message.skill && <SkillChip skill={message.skill} />}
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={mdComponents}
          // The server appends crowd-flow chart PNGs as inline
          // `data:image/png;base64,…` images. react-markdown's default
          // URL transform drops anything that isn't http/https/mailto/etc.
          // so we need to allow `data:` through (otherwise the chart
          // appears as a broken-image icon).
          skipHtml
          urlTransform={safeMarkdownUrl}
        >
          {message.content || '_(empty response)_'}
        </ReactMarkdown>
        {message.content && <CopyButton text={message.content} />}
        {progressRequestId(message.requestId) && <details className="mt-2 text-xs text-brand-grey/60">
          <summary className="cursor-pointer">Response details</summary>
          <p className="mt-1 break-all">Trace: {message.requestId}</p>
        </details>}
      </div>
    </div>
  );
}

// ─── Copy-to-clipboard button (appears under each assistant answer) ────────
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard unavailable — ignore */
    }
  }
  return (
    <div className="mt-3 flex justify-end border-t border-brand-line/60 pt-2">
      <button
        type="button"
        onClick={copy}
        className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium text-brand-grey/45 opacity-0 transition hover:bg-brand-paper hover:text-brand-purple focus:opacity-100 group-hover/msg:opacity-100"
        aria-label="Copy answer"
      >
        {copied ? (
          <>
            <svg viewBox="0 0 16 16" width={12} height={12} fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M13 4L6 11 3 8" /></svg>
            Copied
          </>
        ) : (
          <>
            <svg viewBox="0 0 16 16" width={12} height={12} fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden><rect x="5" y="5" width="8" height="8" rx="1.5" /><path d="M3 10V4a1.5 1.5 0 0 1 1.5-1.5H10" /></svg>
            Copy
          </>
        )}
      </button>
    </div>
  );
}

// ─── Skill chip ──────────────────────────────────────────────────────────
// Shows the skill that was applied to this turn. The skill's prompt was
// injected on the backend into the agent's `instructions` parameter —
// it never enters the user message body, so the chip is the only visible
// trace of *why* the answer is shaped the way it is.
// Small badge showing which agent method produced this answer, so the
// choice in the settings gear is visibly reflected on every response.
const METHOD_LABELS: Record<MethodId, { short: string; full: string }> = {
  M4: { short: 'Copilot SDK · BYOK', full: 'GitHub Copilot SDK with your server-side model provider.' },
  M6: { short: 'M6 · Council',               full: 'M6 · Council — three parallel strategists + verifier + synthesiser.' },
  M7: { short: 'M7 · Work IQ',               full: 'M7 · Work IQ — grounded in your Microsoft 365 via the Work IQ connector.' },
};

function MethodBadge({ method }: { method: MethodId }) {
  const meta = METHOD_LABELS[method];
  if (!meta) return null;
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border border-brand-line bg-brand-paper px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-brand-grey/70"
      title={meta.full}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-brand-purple" />
      {meta.short}
    </span>
  );
}

function SkillChip({ skill }: { skill: SkillRef }) {
  const tooltipBits: string[] = [];
  if (skill.businessAreas.length) {
    tooltipBits.push('Business: ' + skill.businessAreas.join(', '));
  }
  if (skill.regions.length) {
    tooltipBits.push('Region: ' + skill.regions.join(', '));
  }
  tooltipBits.push(
    'Skill prompt injected as additional system instructions on this turn.',
  );
  return (
    <div
      className="mb-2 inline-flex items-center gap-1.5 rounded-full border border-brand-purple/50 bg-brand-purple/5 px-2 py-0.5 text-[11px] text-brand-purple"
      title={tooltipBits.join('\n')}
    >
      <span className="font-medium">Skill</span>
      <span className="text-brand-purple/60">·</span>
      <span>{skill.name}</span>
      {skill.regions.length > 0 && (
        <>
          <span className="text-brand-purple/60">·</span>
          <span className="text-brand-purple/70">{skill.regions.join(', ')}</span>
        </>
      )}
    </div>
  );
}

function AttachmentTag({ att }: { att: AttachmentSummary }) {
  const ext = (/\.([A-Za-z0-9]+)$/.exec(att.name)?.[1] ?? 'doc')
    .toUpperCase()
    .slice(0, 4);
  const sizeLabel =
    att.size < 1024
      ? `${att.size} B`
      : att.size < 1024 * 1024
        ? `${(att.size / 1024).toFixed(1)} KB`
        : `${(att.size / 1024 / 1024).toFixed(1)} MB`;
  const isImage = att.kind === 'image' && Boolean(att.dataUrl);
  return (
    <div
      className="flex max-w-[240px] items-center gap-2 rounded-lg border border-brand-line bg-white px-2 py-1 text-xs text-brand-grey shadow-xs"
      title={`${att.name} · ${sizeLabel}${att.truncated ? ' · truncated' : ''}`}
    >
      {isImage ? (
        // Mirror the composer's chip: a small image thumbnail rather than a
        // FileIcon-style ext label, so the user can recognise the
        // screenshot they dropped in.

        <img
          src={att.dataUrl!}
          alt={att.name}
          className="h-6 w-6 shrink-0 rounded-sm object-cover"
        />
      ) : (
        <span
          className="grid h-6 w-6 shrink-0 place-items-center rounded-sm bg-brand-grey/10 text-[8px] font-mono font-semibold text-brand-grey/70"
          aria-hidden
        >
          {ext}
        </span>
      )}
      <span className="truncate">{att.documentUrl
        ? <DocumentLink href={att.documentUrl}>{att.name}</DocumentLink> : att.name}</span>
      <span className="shrink-0 text-[10px] text-brand-grey/50">{sizeLabel}</span>
    </div>
  );
}

function PendingBubble({ events, method }: { events: ProgressEvent[]; method?: MethodId }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const start = Date.now();
    const id = setInterval(() => {
      setElapsed(Math.floor((Date.now() - start) / 1000));
    }, 500);
    return () => clearInterval(id);
  }, []);

  // Last event drives the headline; the rest go in a small log underneath.
  const headline = events.length > 0
    ? progressHeadline(events[events.length - 1]!)
    : 'Connecting…';
  const activity = events.filter(event => event.phase !== 'delta');

  return (
    <div className="flex justify-start">
      <div className="chat-bubble-asst w-full max-w-full">
        {method && (
          <div className="mb-2">
            <MethodBadge method={method} />
          </div>
        )}
        <div className="flex items-center gap-3 text-sm text-brand-grey/80">
          <span aria-hidden className="inline-block h-2 w-2 motion-safe:animate-pulse rounded-full bg-brand-magenta" />
          <span role="status" aria-live="polite" aria-atomic="true" className="font-medium">{headline}</span>
          <span className="ml-auto font-mono text-xs text-brand-grey/40">
            {elapsed}s
          </span>
        </div>
        {events.find(event => event.phase === 'delta') && <div className="mt-3" aria-label="Response in progress">
          <ReactMarkdown skipHtml urlTransform={safeMarkdownUrl} components={mdComponents}>
            {String(events.find(event => event.phase === 'delta')?.payload.content ?? '')}
          </ReactMarkdown>
        </div>}

        {activity.length > 0 && (
          <details className="mt-2 border-t border-brand-line/60 pt-2 text-xs text-brand-grey/60">
            <summary className="cursor-pointer">Activity details</summary>
            <ol className="mt-2 max-h-48 space-y-1 overflow-y-auto">
            {activity.map((e, i) => (
              <li key={i} className="flex gap-2">
                <span className="font-mono text-brand-grey/40 shrink-0">
                  {String(i + 1).padStart(2, '0')}
                </span>
                <span className="break-all">{progressDetail(e)}</span>
              </li>
            ))}
            </ol>
          </details>
        )}
      </div>
    </div>
  );
}

// ─── Domain-grouped starter prompts (enterprise card format) ──────────────
// Four EnterpriseKG domains, each a black enterprise card with a purple/turquoise
// dot, uppercase tag, white heading and three example prompts.
type DomainKey = 'people' | 'finance' | 'client' | 'projects';

interface DomainGroup {
  key:     DomainKey;
  title:   string;
  blurb:   string;
  dot:     string;   // status-dot + icon colour
  icon:    ReactNode;
  prompts: string[];
  more:    string[];  // extra questions shown in the "More examples" panel
}

const DOMAIN_GROUPS: DomainGroup[] = [
  {
    key: 'people', title: 'People', blurb: 'Staff, practices, skills & capability',
    dot: 'text-brand-purple', icon: <PeopleIcon />,
    prompts: [
      'How many active staff are in each practice?',
      'For the water practice, how many people have the Flood Risk Modelling skill at advanced or expert level?',
      'Which practice networks have the most members, and who are the practice leaders?',
    ],
    more: [
      'How many staff are in each grade, split by discipline?',
      'Which offices and regions have the most active staff?',
      'Who are the top 10 people by years of experience in a given capability?',
      'How many staff are accredited in their primary capability?',
      'Which managers have the largest teams?',
    ],
  },
  {
    key: 'finance', title: 'Finance', blurb: 'Fee budget, forecast, billing & margin',
    dot: 'text-brand-turquoise', icon: <FinanceIcon />,
    prompts: [
      'What is the total fee budget across all active projects?',
      'Show fee forecast versus fee billed by market for active projects.',
      'Rank the 20 worst active projects by margin forecast percent, with project director and client.',
    ],
    more: [
      'What is total fee forecast versus fee billed across the active portfolio?',
      'Which projects have the largest gap between fee budget and fee forecast?',
      'What is the total risk value and change value across active projects?',
      'What is the average margin forecast percent by market?',
      'How many projects are forecasting a margin below 5 percent?',
    ],
  },
  {
    key: 'client', title: 'Client', blurb: 'Accounts, sectors & relationships',
    dot: 'text-brand-purple', icon: <ClientIcon />,
    prompts: [
      'List our strategic clients by sector and the number of active projects for each.',
      'Which clients have active projects with margin forecast below 12 percent?',
      'Who are the relationship directors for our top clients by combined fee budget?',
    ],
    more: [
      'Which clients hold the largest total backlog value?',
      'How many strategic versus framework clients do we have, by sector?',
      'Which countries have the most active clients?',
      'Which relationship directors cover the most clients?',
      'List our dormant and prospect clients we could re-engage.',
    ],
  },
  {
    key: 'projects', title: 'Projects', blurb: 'Pipeline, programmes, staffing & delivery',
    dot: 'text-brand-turquoise', icon: <ProjectsIcon />,
    prompts: [
      'How many active projects do we have across the portfolio, and their combined fee budget?',
      'Show the count of active projects by sector for transport and water.',
      'How many active projects have BIM enabled, and which are digital-twin-enabled?',
    ],
    more: [
      'How many projects are Mobilising versus Closing versus On Hold?',
      'Which frameworks or contract models carry the most active projects?',
      'Where are our carbon specialists and digital specialists concentrated?',
      'What is the average percent complete by sector?',
      'Which project directors are leading the most active projects?',
    ],
  },
];

function EmptyState({ onPick }: { onPick?: (text: string) => void }) {
  const [showMore, setShowMore] = useState(false);
  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-10 sm:py-14">
      <div className="mx-auto max-w-2xl text-center">
        <span className="inline-flex items-center gap-2 rounded-full border border-brand-line bg-white px-3 py-1 text-[11px] font-bold uppercase tracking-wider text-brand-grey/60 shadow-xs">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand-purple" />
          Configured sources · Enterprise IQ
        </span>
        <h1 className="mt-5 font-display text-3xl font-extrabold tracking-tight text-brand-grey sm:text-[2.6rem] sm:leading-9">
          What would you like to know?
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-brand-grey/55 sm:text-base sm:leading-6">
          Ask across the four Example Enterprise domains. Pick a starter below or
          type your own question. Answers use configured sources; demo data is explicitly labelled.
        </p>
      </div>

      <div className="mt-10 grid grid-cols-1 gap-5 md:grid-cols-2">
        {DOMAIN_GROUPS.map((g, gi) => (
          <section
            key={g.key}
            className="brand-corner animate-fade-in-up bg-brand-grey p-6 text-white shadow-card transition hover:shadow-card-hover"
            style={{ animationDelay: `${gi * 70}ms` }}
          >
            <div className="mb-4 flex items-center gap-3">
              <span className={`flex h-6 items-center ${g.dot}`} aria-hidden>
                <span className="h-2.5 w-2.5 rounded-full bg-current" />
              </span>
              <div className="min-w-0">
                <h2 className="font-display text-lg font-extrabold uppercase leading-tight tracking-tight text-white">
                  {g.title}
                </h2>
                <p className="truncate text-xs text-white/50">{g.blurb}</p>
              </div>
              <span className={`ml-auto ${g.dot} opacity-70`}>{g.icon}</span>
            </div>
            <ul className="space-y-2">
              {g.prompts.map((p) => (
                <li key={p}>
                  <button
                    type="button"
                    onClick={() => onPick?.(p)}
                    disabled={!onPick}
                    className="group flex w-full items-start gap-2.5 rounded-xl border border-white/10 bg-white/5 px-3 py-2.5 text-left text-[13px] leading-snug text-white/75 transition hover:border-brand-purple/60 hover:bg-white/10 hover:text-white focus:outline-hidden focus:ring-2 focus:ring-brand-purple/40 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <span className={`mt-0.5 shrink-0 ${g.dot} opacity-60 transition group-hover:translate-x-0.5 group-hover:opacity-100`}>
                      <ArrowIcon />
                    </span>
                    <span>{p}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>

      {/* More example questions — collapsible, grouped by domain */}
      <div className="mt-6 flex justify-center">
        <button
          type="button"
          onClick={() => setShowMore((v) => !v)}
          className="inline-flex items-center gap-2 rounded-full border border-brand-line bg-white px-4 py-2 text-xs font-bold uppercase tracking-wide text-brand-grey/70 shadow-xs transition hover:border-brand-purple/50 hover:text-brand-purple"
          aria-expanded={showMore}
        >
          {showMore ? 'Hide extra examples' : 'More example questions'}
          <span className={`transition-transform ${showMore ? 'rotate-180' : ''}`} aria-hidden>
            <ChevronIcon />
          </span>
        </button>
      </div>

      {showMore && (
        <div className="mt-5 grid grid-cols-1 gap-5 md:grid-cols-2">
          {DOMAIN_GROUPS.map((g) => (
            <section
              key={g.key}
              className="brand-corner animate-fade-in-up border border-brand-line bg-white p-5 shadow-card"
            >
              <div className="mb-3 flex items-center gap-2.5">
                <span className={`flex items-center ${g.dot}`} aria-hidden>
                  <span className="h-2.5 w-2.5 rounded-full bg-current" />
                </span>
                <h3 className="font-display text-sm font-extrabold uppercase tracking-tight text-brand-grey">
                  {g.title}
                </h3>
              </div>
              <ul className="flex flex-wrap gap-2">
                {g.more.map((q) => (
                  <li key={q}>
                    <button
                      type="button"
                      onClick={() => onPick?.(q)}
                      disabled={!onPick}
                      className="rounded-full border border-brand-line bg-brand-paper/60 px-3 py-1.5 text-left text-[12px] leading-snug text-brand-grey/75 transition hover:border-brand-purple/50 hover:bg-white hover:text-brand-grey focus:outline-hidden focus:ring-2 focus:ring-brand-purple/30 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {q}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}

      <p className="mt-8 text-center text-xs text-brand-grey/40">
        Tip: you can also drop a document into the composer to ground an answer in it.
      </p>
    </div>
  );
}

// ─── Domain + arrow icons (inline SVG, no deps) ───────────────────────────
function ChevronIcon() {
  return (
    <svg viewBox="0 0 16 16" width={12} height={12} fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M4 6l4 4 4-4" />
    </svg>
  );
}
function PeopleIcon() {
  return (
    <svg viewBox="0 0 24 24" width={18} height={18} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="9" cy="8" r="3" /><path d="M15 11a3 3 0 1 0-2-5.2" /><path d="M3 20a6 6 0 0 1 12 0" /><path d="M16 14a6 6 0 0 1 5 6" />
    </svg>
  );
}
function FinanceIcon() {
  return (
    <svg viewBox="0 0 24 24" width={18} height={18} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M3 3v18h18" /><path d="M7 15l4-4 3 3 5-6" />
    </svg>
  );
}
function ClientIcon() {
  return (
    <svg viewBox="0 0 24 24" width={18} height={18} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M3 21V8l7-4 7 4v13" /><path d="M9 21v-5h4v5" /><path d="M17 10h4v11H3" />
    </svg>
  );
}
function ProjectsIcon() {
  return (
    <svg viewBox="0 0 24 24" width={18} height={18} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 9h18" /><path d="M8 13h5" /><path d="M8 16h8" />
    </svg>
  );
}
function ArrowIcon() {
  return (
    <svg viewBox="0 0 16 16" width={13} height={13} fill="none" stroke="currentColor" strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M4 8h8M9 4l4 4-4 4" />
    </svg>
  );
}
