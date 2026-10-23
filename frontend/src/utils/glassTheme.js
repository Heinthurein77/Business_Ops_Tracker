// "Command" design language — crisp executive surfaces.
//
// Replaces the old frosted-glass look (backdrop-blur, 20px radii, gradient
// page backgrounds) with solid surfaces, hairline borders and tight radii,
// in the spirit of Linear / Stripe dashboards: calmer, sharper, denser.
// Export names (GLASS, PAGE_BG) are kept so the 15 files importing them need
// no changes — only the values changed.
export const GLASS =
  'bg-white dark:bg-slate-900 rounded-xl border border-slate-200/90 dark:border-slate-800 ' +
  'shadow-[0_1px_2px_rgba(16,24,40,0.05),0_2px_8px_-2px_rgba(16,24,40,0.05)]';

// Flat page background — no gradients. Light: cool slate tint; dark: near-black.
export const PAGE_BG = 'bg-[#edf0f5] dark:bg-slate-950';

// Shared page header: title block + actions row.
export const PAGE_HEADER = 'flex items-start justify-between flex-wrap gap-4 mb-6';

// Section title inside cards.
export const CARD_TITLE = 'text-[15px] font-bold text-slate-900 dark:text-slate-100 tracking-tight';

// Muted description text.
export const MUTED = 'text-[13px] text-slate-500 dark:text-slate-400';
