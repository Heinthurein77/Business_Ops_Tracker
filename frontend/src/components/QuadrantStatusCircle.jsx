import { useId } from 'react';

// Fixed status palette — distinct from the app's categorical/brand hues so a
// status color never doubles as a series color. Matches the small status
// dots used in every legend (bg-emerald-500 / bg-amber-500 / bg-red-500 /
// bg-slate-300) so the wedge fill and its legend swatch are the same hex.
const STATUS_COLORS = {
  green: '#10b981',
  yellow: '#f59e0b',
  red: '#ef4444',
  grey: '#cbd5e1',
  unknown: '#cbd5e1',
};

// A circle quartered by its horizontal/vertical diameters into 4 fixed sectors:
// Financial (top-left), Social (top-right), Activity (bottom-left), Health (bottom-right).
// labelArc is an invisible radius-36 guide path (left endpoint -> right
// endpoint, both left-to-right so textPath renders the label upright)
// each quadrant's label curves along, badge/seal style, instead of sitting flat.
const QUADRANTS = [
  { key: 'financial', label: 'Business Performance', path: 'M50,50 L50,5 A45,45 0 0,0 5,50 Z', labelArc: 'M14,50 A36,36 0 0,1 50,14' },
  { key: 'social', label: 'Social Activity', path: 'M50,50 L95,50 A45,45 0 0,0 50,5 Z', labelArc: 'M50,14 A36,36 0 0,1 86,50' },
  { key: 'activity', label: 'Daily Activities', path: 'M50,50 L5,50 A45,45 0 0,0 50,95 Z', labelArc: 'M14,50 A36,36 0 0,0 50,86' },
  { key: 'health', label: 'Health and Fitness', path: 'M50,50 L50,95 A45,45 0 0,0 95,50 Z', labelArc: 'M50,86 A36,36 0 0,0 86,50' },
];

function capitalize(status) {
  if (!status || status === 'unknown' || status === 'grey') return 'No data';
  return status.charAt(0).toUpperCase() + status.slice(1);
}

// Greedily wraps the center label (a short "BU2" code, or a full business
// name like "ABC Logistics (Logistics)") into up to 3 lines targeting even
// line lengths, then derives a font size from the longest resulting line so
// it stays within the r=24 center circle regardless of name length — a
// fixed font size works for "HO" but not for "MIB FMCG Principal (Shiny)".
function wrapCenterLabel(label) {
  const words = label.trim().split(/\s+/);
  const maxLines = 3;
  const targetPerLine = Math.max(1, Math.ceil(label.length / maxLines));

  const lines = [];
  let current = '';
  for (const word of words) {
    const candidate = current ? `${current} ${word}` : word;
    if (candidate.length > targetPerLine && current) {
      lines.push(current);
      current = word;
    } else {
      current = candidate;
    }
  }
  if (current) lines.push(current);
  while (lines.length > maxLines) {
    const last = lines.pop();
    lines[lines.length - 1] += ` ${last}`;
  }

  const maxLineLen = Math.max(...lines.map((l) => l.length));
  // Solved for a ~34-unit target line width at an estimated 0.58em average
  // glyph width for this bold uppercase-ish label text.
  const fontSize = Math.min(16, Math.max(4.5, 34 / (maxLineLen * 0.58)));
  return {
    // A trailing "(Distribution)"-style qualifier is secondary information —
    // same hierarchy as the bold-code/muted-name pairs used everywhere else
    // in the app (Report Logs, Operations Grid, etc.) — so it renders lighter
    // and a touch smaller than the primary name lines, not with equal weight.
    lines: lines.map((text) => ({ text, muted: /^\(.+\)$/.test(text) })),
    fontSize,
  };
}

export default function QuadrantStatusCircle({ financial, social, activity, health, size = 36, label, showLabels = false }) {
  const statuses = { financial, social, activity, health };
  const title = QUADRANTS.map((q) => `${q.label}: ${capitalize(statuses[q.key])}`).join(' · ');
  const uid = useId();
  const centerLabel = label ? wrapCenterLabel(label) : null;

  return (
    <svg width={size} height={size} viewBox="0 0 100 100" role="img" aria-label={title} className="flex-shrink-0 overflow-visible">
      <title>{title}</title>
      <defs>
        {showLabels &&
          QUADRANTS.map((q) => (
            <path key={`${q.key}-arc`} id={`${uid}-${q.key}-arc`} d={q.labelArc} fill="none" />
          ))}
        {/* Soft elevation instead of a hard outer border — reads as a card
            lifted off the page rather than a flat sticker. */}
        <filter id={`${uid}-shadow`} x="-30%" y="-30%" width="160%" height="160%">
          <feDropShadow dx="0" dy="1.5" stdDeviation="2.5" floodColor="#0f172a" floodOpacity="0.18" />
        </filter>
      </defs>

      <g filter={`url(#${uid}-shadow)`}>
        {QUADRANTS.map((q) => (
          <path
            key={q.key}
            d={q.path}
            fill={STATUS_COLORS[statuses[q.key]] || STATUS_COLORS.grey}
            className="stroke-white dark:stroke-slate-900"
            strokeWidth="2.5"
            strokeLinejoin="round"
          />
        ))}
        {/* Hairline ring in place of the old heavy dark outline — a seam, not a border. */}
        <circle cx="50" cy="50" r="47" fill="none" className="stroke-slate-900/10 dark:stroke-white/10" strokeWidth="1" />
      </g>

      {showLabels &&
        QUADRANTS.map((q) => (
          <text
            key={`${q.key}-label`}
            fontSize="5.5"
            fontWeight="600"
            fill="#ffffff"
            stroke="rgba(15,23,42,0.35)"
            strokeWidth="0.22"
            paintOrder="stroke"
            letterSpacing="0.2"
            style={{ textTransform: 'uppercase' }}
          >
            {/* textLength+lengthAdjust force every label to occupy the same
                arc width regardless of character count — without it, the
                longer renamed labels (e.g. "Health and Fitness" vs the
                original "Health") would overrun the 36-radius quarter-arc
                and overlap the neighboring quadrant's text. */}
            <textPath href={`#${uid}-${q.key}-arc`} startOffset="50%" textAnchor="middle" textLength="47" lengthAdjust="spacingAndGlyphs">
              {q.label}
            </textPath>
          </text>
        ))}

      {centerLabel && (
        <g filter={`url(#${uid}-shadow)`}>
          <circle cx="50" cy="50" r="24" className="fill-white dark:fill-slate-800 stroke-slate-200 dark:stroke-slate-600" strokeWidth="1.5" />
          <text x="50" textAnchor="middle" fontWeight="600" fontSize={centerLabel.fontSize} letterSpacing="0.1" className="fill-slate-800 dark:fill-slate-100">
            {centerLabel.lines.map((line, i) => (
              <tspan
                key={i}
                x="50"
                y={50 + (i - (centerLabel.lines.length - 1) / 2) * centerLabel.fontSize * 1.1}
                dominantBaseline="middle"
                fontSize={line.muted ? centerLabel.fontSize * 0.86 : centerLabel.fontSize}
                fontWeight={line.muted ? '500' : '600'}
                className={line.muted ? 'fill-slate-400' : ''}
              >
                {line.text}
              </tspan>
            ))}
          </text>
        </g>
      )}
    </svg>
  );
}

export { STATUS_COLORS };
