import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

const WEEKDAY_LABELS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'];
const MONTH_LABELS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const PANEL_WIDTH = 288; // w-72
const PANEL_MARGIN = 8;

function isoOf(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function parseIso(iso) {
  if (!iso) return null;
  const [y, m, d] = iso.split('-').map(Number);
  if (!y || !m || !d) return null;
  return new Date(y, m - 1, d);
}

function buildGrid(year, month) {
  const firstOfMonth = new Date(year, month, 1);
  const startWeekday = (firstOfMonth.getDay() + 6) % 7; // 0 = Monday
  const cells = [];
  for (let i = 0; i < 42; i++) {
    const d = new Date(year, month, i - startWeekday + 1);
    cells.push({ date: d, iso: isoOf(d), inMonth: d.getMonth() === month });
  }
  return cells;
}

function formatDisplay(iso) {
  const d = parseIso(iso);
  if (!d) return '';
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
}

/** Reusable calendar-dropdown date picker — a styled replacement for the
 * native <input type="date">, whose popup calendar can't be themed and reads
 * as inconsistent with the rest of the app. Value/onChange use plain ISO
 * (YYYY-MM-DD) strings so it drops into existing form state unchanged.
 *
 * The panel renders through a portal into document.body and is positioned
 * with `fixed` coordinates computed from the trigger's own bounding rect
 * (flipping above/left when it would overflow the viewport) — plain
 * `absolute` positioning inside the trigger would get silently clipped by
 * any ancestor with `overflow-hidden` (e.g. the rounded-corner GLASS
 * sections used throughout the admin pages), which is what caused the
 * calendar to render cut off at the edge of its containing card.
 *
 * @param variant 'light' (default) follows the admin section's light/dark
 *   toggle via `dark:` classes; 'dark' forces the always-dark CEO-facing look
 *   (those pages don't participate in the toggle). */
export default function DatePicker({ value, onChange, min, max, placeholder = 'Select date', variant = 'light', className = '' }) {
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState(null);
  const selectedDate = parseIso(value);
  const [viewYear, setViewYear] = useState((selectedDate || new Date()).getFullYear());
  const [viewMonth, setViewMonth] = useState((selectedDate || new Date()).getMonth());
  const triggerRef = useRef(null);
  const panelRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    const d = parseIso(value) || new Date();
    setViewYear(d.getFullYear());
    setViewMonth(d.getMonth());
  }, [open, value]);

  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return;
    function reposition() {
      const rect = triggerRef.current.getBoundingClientRect();
      const panelHeight = panelRef.current?.offsetHeight ?? 360;
      const overflowsRight = rect.left + PANEL_WIDTH > window.innerWidth - PANEL_MARGIN;
      const overflowsBottom = rect.bottom + panelHeight > window.innerHeight - PANEL_MARGIN;
      const left = overflowsRight ? Math.max(PANEL_MARGIN, rect.right - PANEL_WIDTH) : rect.left;
      const top = overflowsBottom ? rect.top - panelHeight - PANEL_MARGIN : rect.bottom + PANEL_MARGIN;
      setCoords({ top, left });
    }
    reposition();
    window.addEventListener('resize', reposition);
    window.addEventListener('scroll', reposition, true);
    return () => {
      window.removeEventListener('resize', reposition);
      window.removeEventListener('scroll', reposition, true);
    };
  }, [open, viewMonth, viewYear]);

  useEffect(() => {
    function handleOutsideClick(e) {
      if (triggerRef.current?.contains(e.target)) return;
      if (panelRef.current?.contains(e.target)) return;
      setOpen(false);
    }
    document.addEventListener('mousedown', handleOutsideClick);
    return () => document.removeEventListener('mousedown', handleOutsideClick);
  }, []);

  useEffect(() => {
    if (!open) return;
    function handleKey(e) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [open]);

  const minDate = parseIso(min);
  const maxDate = parseIso(max);
  const grid = useMemo(() => buildGrid(viewYear, viewMonth), [viewYear, viewMonth]);

  const yearFrom = minDate ? minDate.getFullYear() : new Date().getFullYear() - 100;
  const yearTo = maxDate ? maxDate.getFullYear() : new Date().getFullYear();
  const yearOptions = [];
  for (let y = yearTo; y >= yearFrom; y--) yearOptions.push(y);

  function goMonth(delta) {
    const d = new Date(viewYear, viewMonth + delta, 1);
    setViewYear(d.getFullYear());
    setViewMonth(d.getMonth());
  }

  function pick(iso) {
    onChange(iso);
    setOpen(false);
  }

  const isDark = variant === 'dark';
  const triggerClass = isDark
    ? `w-full flex items-center justify-between gap-2 rounded-lg border bg-slate-950 text-white px-4 py-2.5 text-sm transition-colors focus:outline-none ${
        open ? 'border-brand-500 ring-2 ring-brand-500/30' : 'border-slate-700 hover:border-slate-600'
      }`
    : `w-full flex items-center justify-between gap-2 rounded-lg border bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-300 px-3.5 py-2.5 text-sm transition-colors focus:outline-none ${
        open ? 'border-brand-500 ring-2 ring-brand-500/30' : 'border-slate-200 dark:border-slate-700 hover:border-slate-300 dark:hover:border-slate-600'
      }`;
  const panelClass = isDark
    ? 'bg-slate-900 border border-slate-700/80 text-slate-200'
    : 'bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 text-slate-700 dark:text-slate-300';
  const mutedText = isDark ? 'text-slate-500' : 'text-slate-400 dark:text-slate-500';
  const selectClass = isDark
    ? 'bg-slate-800 border border-slate-700 text-slate-200 text-xs font-semibold rounded-lg pl-2.5 pr-1.5 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-500 cursor-pointer'
    : 'bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300 text-xs font-semibold rounded-lg pl-2.5 pr-1.5 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-500 cursor-pointer';
  const navBtnClass = isDark
    ? 'w-7 h-7 flex items-center justify-center rounded-full hover:bg-slate-800 active:bg-slate-700 text-slate-400 hover:text-slate-200 transition-colors'
    : 'w-7 h-7 flex items-center justify-center rounded-full hover:bg-slate-100 dark:hover:bg-slate-800 active:bg-slate-200 dark:active:bg-slate-700 text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 transition-colors';

  return (
    <>
      <div className={`relative ${className}`}>
        <button type="button" ref={triggerRef} onClick={() => setOpen((v) => !v)} className={triggerClass}>
          <span className={value ? 'font-medium' : mutedText}>{value ? formatDisplay(value) : placeholder}</span>
          <svg xmlns="http://www.w3.org/2000/svg" className={`w-4 h-4 flex-shrink-0 ${value && !isDark ? 'text-brand-500' : value && isDark ? 'text-brand-400' : mutedText}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
          </svg>
        </button>
      </div>

      {open &&
        createPortal(
          <div
            ref={panelRef}
            style={{ position: 'fixed', top: coords?.top ?? -9999, left: coords?.left ?? -9999, width: PANEL_WIDTH, visibility: coords ? 'visible' : 'hidden' }}
            className={`z-[999] rounded-xl shadow-2xl shadow-slate-900/20 p-4 fade-in ${panelClass}`}
          >
            <div className="flex items-center justify-between gap-1.5 mb-3">
              <button type="button" onClick={() => goMonth(-1)} className={navBtnClass} aria-label="Previous month">
                <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" /></svg>
              </button>
              <div className="flex items-center gap-1.5 min-w-0">
                <select value={viewMonth} onChange={(e) => setViewMonth(Number(e.target.value))} className={`${selectClass} min-w-0`}>
                  {MONTH_LABELS.map((m, i) => (
                    <option key={m} value={i}>{m}</option>
                  ))}
                </select>
                <select value={viewYear} onChange={(e) => setViewYear(Number(e.target.value))} className={selectClass}>
                  {yearOptions.map((y) => (
                    <option key={y} value={y}>{y}</option>
                  ))}
                </select>
              </div>
              <button type="button" onClick={() => goMonth(1)} className={navBtnClass} aria-label="Next month">
                <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2"><path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" /></svg>
              </button>
            </div>

            <div className="grid grid-cols-7 text-center text-[10px] font-semibold text-slate-400 dark:text-slate-500 mb-1.5">
              {WEEKDAY_LABELS.map((label) => (
                <div key={label}>{label}</div>
              ))}
            </div>

            <div className="grid grid-cols-7 gap-y-0.5">
              {grid.map((cell) => {
                const disabled = (minDate && cell.date < minDate) || (maxDate && cell.date > maxDate);
                const isSelected = value === cell.iso;
                const isToday = isoOf(new Date()) === cell.iso;
                return (
                  <div key={cell.iso} className="flex justify-center py-0.5">
                    <button
                      type="button"
                      disabled={disabled}
                      onClick={() => pick(cell.iso)}
                      className={`w-8 h-8 rounded-full text-xs flex items-center justify-center transition-colors ${
                        isSelected
                          ? 'bg-brand-600 text-white font-bold shadow-sm shadow-brand-600/40'
                          : disabled
                          ? 'text-slate-300 dark:text-slate-700 cursor-not-allowed'
                          : cell.inMonth
                          ? `text-slate-700 dark:text-slate-300 hover:bg-brand-500/10 hover:text-brand-600 dark:hover:text-brand-400 ${isToday ? 'font-bold ring-1 ring-inset ring-brand-400/60' : ''}`
                          : 'text-slate-300 dark:text-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800'
                      }`}
                    >
                      {cell.date.getDate()}
                    </button>
                  </div>
                );
              })}
            </div>

            {value && (
              <button
                type="button"
                onClick={() => pick(null)}
                className={`mt-3 pt-3 border-t w-full text-center text-[11px] font-semibold transition-colors ${
                  isDark
                    ? 'border-slate-800 text-slate-500 hover:text-slate-300'
                    : 'border-slate-100 dark:border-slate-800 text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300'
                }`}
              >
                Clear date
              </button>
            )}
          </div>,
          document.body
        )}
    </>
  );
}
