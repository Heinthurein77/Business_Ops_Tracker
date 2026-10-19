import { useTheme } from '../context/ThemeContext';

function ToggleIcon({ d, className = 'w-4 h-4' }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" className={`${className} flex-shrink-0`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
      <path strokeLinecap="round" strokeLinejoin="round" d={d} />
    </svg>
  );
}

/** Light/dark toggle for the admin section — flips the `dark` class on
 * <html> via ThemeContext. Pass `iconOnly` for compact placements (e.g. a
 * page header) instead of the labelled sidebar/mobile-menu form. */
export default function ThemeToggle({ className = '', iconOnly = false }) {
  const { theme, toggleTheme } = useTheme();
  const isDark = theme === 'dark';
  const iconPath = isDark
    ? 'M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z'
    : 'M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z';

  return (
    <button
      type="button"
      onClick={toggleTheme}
      aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      className={`flex items-center gap-2.5 text-xs font-semibold text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 transition-colors ${className}`}
    >
      <ToggleIcon d={iconPath} />
      {!iconOnly && (isDark ? 'Light Mode' : 'Dark Mode')}
    </button>
  );
}
