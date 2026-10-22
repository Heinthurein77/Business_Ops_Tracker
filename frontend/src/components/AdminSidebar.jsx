import { useState } from 'react';
import { NavLink } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import ThemeToggle from './ThemeToggle';
import NotificationBell from './NotificationBell';

const ADMIN_NAV_GROUPS = [
  {
    label: 'Overview',
    links: [
      { to: '/admin', label: 'Executive Dashboard', end: true },
      { to: '/admin/comparison', label: 'Comparison' },
    ],
  },
  {
    label: 'Track',
    links: [
      { to: '/admin/activity-dashboard', label: 'Daily Activities' },
      { to: '/admin/ceo-calendar', label: 'CEO Calendar' },
      { to: '/admin/health-dashboard', label: 'Health and Fitness' },
      { to: '/admin/operations', label: 'Operations' },
      { to: '/admin/report-logs', label: 'Report Logs' },
    ],
  },
  {
    label: 'Manage',
    links: [
      { to: '/admin/operations-center', label: 'Operations Center' },
      { to: '/accounting', label: 'Accounting' },
      { to: '/admin/ceo-profiles', label: 'CEO Profiles' },
      { to: '/admin/head-office', label: 'Head Office' },
      { to: '/admin/business-units', label: 'Business Units' },
      { to: '/admin/users', label: 'User Management', chairmanOnly: true },
      { to: '/admin/security-log', label: 'Security Log', chairmanOnly: true },
    ],
  },
];

// CEO's own section — same sidebar shell as Chairman/HO, its own small link
// set (mirrors what CeoNav used to offer as a horizontal bar).
const CEO_NAV_GROUPS = [
  {
    label: 'My Workspace',
    links: [
      { to: '/ceo', label: 'Dashboard', end: true },
      { to: '/ceo/history', label: 'History' },
      { to: '/ceo/report-logs', label: 'Report Logs' },
    ],
  },
];

// Accountant (ERP Phase 1) — accounting section only.
const ACCOUNTANT_NAV_GROUPS = [
  {
    label: 'Finance',
    links: [
      { to: '/accounting', label: 'Accounting', end: true },
    ],
  },
];

const ICONS = {
  '/admin': 'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z',
  '/admin/comparison': 'M8 7h12m0 0l-4-4m4 4l-4 4M16 17H4m0 0l4 4m-4-4l4-4',
  '/admin/operations': 'M3 3h7v7H3V3zm11 0h7v7h-7V3zM3 14h7v7H3v-7zm11 0h7v7h-7v-7z',
  '/admin/activity-dashboard': 'M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z',
  '/admin/ceo-calendar': 'M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z',
  '/admin/health-dashboard': 'M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z',
  '/admin/report-logs': 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z',
  '/admin/ceo-profiles': 'M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z',
  '/admin/operations-center': 'M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065zM15 12a3 3 0 11-6 0 3 3 0 016 0z',
  '/accounting': 'M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
  '/admin/head-office': 'M3 21h18M5 21V7l7-4 7 4v14M9 9h1m-1 4h1m4-4h1m-1 4h1M9 21v-4h6v4',
  '/admin/users': 'M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z',
  '/admin/business-units': 'M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4',
  '/admin/security-log': 'M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z',
  '/ceo': 'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z',
  '/ceo/history': 'M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z',
  '/ceo/report-logs': 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z',
};

function NavIcon({ d, className = 'w-4 h-4' }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" className={`${className} flex-shrink-0`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
      <path strokeLinecap="round" strokeLinejoin="round" d={d} />
    </svg>
  );
}

/** The abcMIB brand mark — a dedicated monogram badge, not the same "grid"
 * glyph reused elsewhere for the Executive Dashboard nav link, so the logo
 * reads as an identity rather than borrowing a nav icon. Three-stop gradient
 * + inset ring + soft shadow give it depth instead of a flat color fill. */
function BrandMark({ size = 'w-9 h-9', rounding = 'rounded-xl', textClass = 'text-[15px]', shadow = 'shadow-lg' }) {
  return (
    <div
      className={`${size} ${rounding} bg-gradient-to-br from-brand-400 via-brand-600 to-indigo-700 flex items-center justify-center ${shadow} shadow-brand-500/30 ring-1 ring-inset ring-white/25 flex-shrink-0`}
    >
      <span className={`text-white font-extrabold ${textClass} tracking-tight leading-none`}>A</span>
    </div>
  );
}

/** Shared vertical glassmorphism navigation for every admin route — replaces
 * the old horizontal dark AdminNav top bar. Renders a sticky full-height
 * sidebar on desktop and a collapsible top bar + dropdown panel on mobile. */
export function displayNameOf(user) {
  const fullName = user?.full_name?.trim();
  return fullName || user?.username || '';
}

function initialsOf(user) {
  const name = displayNameOf(user);
  if (!name) return '?';
  const words = name.trim().split(/\s+/);
  return words.length > 1 ? (words[0][0] + words[1][0]).toUpperCase() : name.slice(0, 2).toUpperCase();
}

export default function AdminSidebar() {
  const { user, logout } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);
  const displayName = displayNameOf(user);
  const initials = initialsOf(user);
  // HO Staff sits under the Chairman in the org structure even though both
  // roles currently share identical admin permissions — this is display-only.
  const reportsTo = user?.role === 'HO' ? 'Reports to Chairman' : null;
  const isCeo = user?.role === 'CEO';
  const isAccountant = user?.role === 'Accountant';
  const navGroups = isCeo ? CEO_NAV_GROUPS : isAccountant ? ACCOUNTANT_NAV_GROUPS : ADMIN_NAV_GROUPS;
  const visibleGroups = navGroups
    .map((g) => ({
      ...g,
      links: g.links.filter((link) => !link.chairmanOnly || user?.role === 'Chairman'),
    }))
    .filter((g) => g.links.length > 0);

  const navLinkClass = ({ isActive }) =>
    `flex items-center gap-3 text-[13px] font-medium px-3 py-2 rounded-lg transition-colors ${
      isActive
        ? 'bg-brand-600 text-white shadow-sm'
        : 'text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 hover:text-slate-900 dark:hover:text-slate-100'
    }`;

  return (
    <>
      {/* DESKTOP SIDEBAR — solid surface, hairline right border, grouped nav */}
      <aside className="hidden lg:flex flex-col w-60 flex-shrink-0 h-screen sticky top-0 bg-white dark:bg-slate-900 border-r border-slate-200 dark:border-slate-800">
        <div className="flex items-center gap-2.5 px-5 pt-5 pb-4">
          <BrandMark size="w-8 h-8" rounding="rounded-lg" textClass="text-[13px]" shadow="shadow-sm" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-bold text-slate-900 dark:text-slate-100 leading-none truncate tracking-tight">abcMIB</p>
            <p className="text-[10px] text-slate-400 mt-1 truncate">Business Ops Tracker</p>
          </div>
          <NotificationBell />
        </div>

        <nav className="flex-1 overflow-y-auto px-3 pb-4 space-y-5">
          {visibleGroups.map((group) => (
            <div key={group.label}>
              <p className="px-3 mb-1.5 text-[10px] font-bold uppercase tracking-[0.12em] text-slate-400 dark:text-slate-500">
                {group.label}
              </p>
              <div className="space-y-0.5">
                {group.links.map((link) => (
                  <NavLink key={link.to} to={link.to} end={link.end} className={navLinkClass}>
                    <NavIcon d={ICONS[link.to]} />
                    <span className="truncate">{link.label}</span>
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>

        <div className="border-t border-slate-200 dark:border-slate-800 p-4 space-y-3">
          <div className="flex items-center gap-2.5">
            <span className="w-8 h-8 rounded-full bg-brand-600 flex items-center justify-center text-[11px] font-bold text-white flex-shrink-0">
              {initials}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-[12px] font-semibold text-slate-800 dark:text-slate-200 truncate">{displayName}</p>
              <p className="text-[10px] text-slate-400 truncate capitalize">{user?.role}</p>
            </div>
            {!isCeo && <ThemeToggle className="flex-shrink-0" />}
          </div>
          <button
            onClick={() => logout()}
            className="w-full text-xs font-medium text-slate-500 hover:text-slate-800 dark:hover:text-slate-200 border border-slate-200 dark:border-slate-700 hover:border-slate-300 dark:hover:border-slate-600 rounded-lg px-3 py-2 transition-colors"
          >
            Sign Out
          </button>
        </div>
      </aside>

      {/* MOBILE TOP BAR */}
      <header className="lg:hidden w-full bg-white dark:bg-slate-900 sticky top-0 z-40 border-b border-slate-200 dark:border-slate-800 px-4 py-2.5">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5 min-w-0">
            <BrandMark size="w-8 h-8" rounding="rounded-lg" textClass="text-[13px]" shadow="shadow-md" />
            <p className="text-xs font-bold text-slate-900 dark:text-slate-100 truncate">abcMIB</p>
          </div>
          <button
            onClick={() => setMobileOpen((v) => !v)}
            className="w-9 h-9 flex items-center justify-center rounded-lg bg-slate-900/5 dark:bg-white/5 text-slate-600 dark:text-slate-300 flex-shrink-0"
            aria-label="Toggle menu"
          >
            {mobileOpen ? <NavIcon d="M6 18L18 6M6 6l12 12" className="w-5 h-5" /> : <NavIcon d="M4 6h16M4 12h16M4 18h16" className="w-5 h-5" />}
          </button>
        </div>

        {mobileOpen && (
          <div className="mt-3 pt-3 border-t border-slate-200 dark:border-slate-800 space-y-4 fade-in">
            {visibleGroups.map((group) => (
              <div key={group.label}>
                <p className="px-3 mb-1 text-[10px] font-bold uppercase tracking-[0.12em] text-slate-400 dark:text-slate-500">
                  {group.label}
                </p>
                <nav className="flex flex-col gap-0.5">
                  {group.links.map((link) => (
                    <NavLink
                      key={link.to}
                      to={link.to}
                      end={link.end}
                      onClick={() => setMobileOpen(false)}
                      className={navLinkClass}
                    >
                      <NavIcon d={ICONS[link.to]} />
                      <span className="truncate">{link.label}</span>
                    </NavLink>
                  ))}
                </nav>
              </div>
            ))}
            {!isCeo && <ThemeToggle className="w-full justify-center bg-slate-900/5 dark:bg-white/5 px-3.5 py-2 rounded-lg" />}
            <div className="flex items-center justify-between gap-3 pt-3 border-t border-slate-200 dark:border-slate-700">
              <span className="flex items-center gap-2 text-xs font-semibold text-slate-700 dark:text-slate-300 bg-slate-900/5 dark:bg-white/5 px-3 py-1.5 rounded-full">
                <span className="w-5 h-5 rounded-full bg-gradient-to-br from-brand-500 to-indigo-400 flex items-center justify-center text-[9px] font-bold text-white flex-shrink-0">
                  {initials}
                </span>
                <span className="capitalize">{user?.role}</span> &middot; {displayName}
                {reportsTo && <> &middot; {reportsTo}</>}
              </span>
              <button
                onClick={() => logout()}
                className="text-xs font-semibold bg-slate-900/5 dark:bg-white/5 hover:bg-slate-900/10 dark:hover:bg-white/10 text-slate-700 dark:text-slate-300 px-3.5 py-2 rounded-lg transition-colors"
              >
                Sign Out
              </button>
            </div>
          </div>
        )}
      </header>
    </>
  );
}
