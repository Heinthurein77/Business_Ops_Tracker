// Shared glassmorphism card treatment for the admin section's theme
// (PayFlow-style redesign) — kept as one constant so every admin page's
// "glass card" look stays in sync in both light and dark mode. Also used by
// the shared AdminSidebar nav. Dark variants activate via the `dark` class
// ThemeContext toggles on <html>.
export const GLASS = 'bg-white/80 dark:bg-slate-900/70 backdrop-blur-md rounded-[20px] shadow-[0_10px_30px_rgba(0,0,0,0.03)] dark:shadow-[0_10px_30px_rgba(0,0,0,0.35)] border border-white/60 dark:border-slate-700/60';

// Page-background gradient used behind the glass cards.
export const PAGE_BG = 'bg-gradient-to-br from-[#eef2f3] to-brand-100 dark:from-slate-950 dark:to-slate-900';
