import { money } from '../utils/quadrantMetrics';

const WEEKDAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

// One chip per non-empty activity field group, in reading order. Colors
// echo Google Calendar's per-calendar event colors: each field group
// keeps its own hue everywhere this component is used.
const CHIP_GROUPS = [
  {
    key: 'meetings_attended',
    label: 'Meeting',
    cls: 'bg-brand-50 border-brand-100 text-brand-700 dark:bg-brand-500/10 dark:border-brand-500/20 dark:text-brand-300',
  },
  {
    key: 'site_visits',
    label: 'Visit',
    cls: 'bg-emerald-50 border-emerald-100 text-emerald-700 dark:bg-emerald-500/10 dark:border-emerald-500/20 dark:text-emerald-300',
  },
  {
    key: 'key_decisions',
    label: 'Decision',
    cls: 'bg-amber-50 border-amber-100 text-amber-700 dark:bg-amber-500/10 dark:border-amber-500/20 dark:text-amber-300',
  },
  {
    key: 'next_day_schedule',
    label: 'Schedule',
    cls: 'bg-violet-50 border-violet-100 text-violet-700 dark:bg-violet-500/10 dark:border-violet-500/20 dark:text-violet-300',
  },
];

const MAX_CHIPS = 3;

function firstLine(text) {
  return (text || '').split('\n').map((s) => s.trim()).find(Boolean) || '';
}

function compactMoney(value) {
  if (value == null) return null;
  if (Math.abs(value) >= 1_000_000) return `Ks ${(value / 1_000_000).toFixed(Math.abs(value) >= 10_000_000 ? 0 : 1)}M`;
  if (Math.abs(value) >= 1_000) return `Ks ${Math.round(value / 1_000)}k`;
  return money(value);
}

function chipsFor(day) {
  return CHIP_GROUPS.filter((g) => firstLine(day[g.key])).map((g) => ({ ...g, text: firstLine(day[g.key]) }));
}

const navButtonCls =
  'w-8 h-8 flex items-center justify-center rounded-full hover:bg-slate-100 dark:hover:bg-slate-800 text-slate-500 dark:text-slate-400 transition-colors';

/**
 * Google Calendar style month view shared by the Chairman/HO "CEO
 * Calendar" page and the CEO's own History page. Purely presentational:
 * the parent owns the data fetch, month state and what a day click does
 * (read-only detail panel for executives, fill/edit for the CEO).
 */
export default function ActivityMonthCalendar({
  calendarWeeks,
  monthLabel,
  onPrevMonth,
  onNextMonth,
  onToday,
  onDayClick,
  selectedDate,
}) {
  return (
    <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-[0_1px_2px_rgba(16,24,40,0.06)] overflow-hidden">
      <div className="flex items-center justify-between gap-3 px-4 sm:px-5 py-3 border-b border-slate-200 dark:border-slate-800">
        <h3 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight text-base sm:text-lg">{monthLabel}</h3>
        <div className="flex items-center gap-1">
          <button type="button" onClick={onPrevMonth} className={navButtonCls} title="Previous month">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          {onToday && (
            <button
              type="button"
              onClick={onToday}
              className="text-xs font-semibold text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 px-3 py-1.5 rounded-full transition-colors"
            >
              Today
            </button>
          )}
          <button type="button" onClick={onNextMonth} className={navButtonCls} title="Next month">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
            </svg>
          </button>
        </div>
      </div>

      <div className="grid grid-cols-7 border-b border-slate-200 dark:border-slate-800">
        {WEEKDAY_LABELS.map((label, i) => (
          <div
            key={label}
            className={`py-2 text-center text-[11px] font-semibold uppercase tracking-wide ${
              i >= 5 ? 'text-slate-400 dark:text-slate-500' : 'text-slate-500 dark:text-slate-400'
            }`}
          >
            {label}
          </div>
        ))}
      </div>

      <div>
        {calendarWeeks.map((week, wi) => (
          <div className={`grid grid-cols-7 ${wi > 0 ? 'border-t border-slate-200 dark:border-slate-800' : ''}`} key={wi}>
            {week.map((day, di) => {
              const chips = chipsFor(day);
              const shown = chips.slice(0, MAX_CHIPS);
              const moreCount = chips.length - shown.length;
              const revenueLabel = compactMoney(day.revenue);
              const isSelected = selectedDate && selectedDate === day.date_iso;
              return (
                <button
                  type="button"
                  key={day.date_iso}
                  onClick={() => onDayClick && onDayClick(day)}
                  className={`relative text-left align-top min-h-[6.25rem] sm:min-h-[7.5rem] p-1.5 sm:p-2 flex flex-col gap-1 transition-colors ${
                    di > 0 ? 'border-l border-slate-200 dark:border-slate-800' : ''
                  } ${day.in_month ? '' : 'bg-slate-50/70 dark:bg-slate-800/30'} ${
                    day.is_weekend && day.in_month ? 'bg-slate-50/40 dark:bg-slate-800/20' : ''
                  } hover:bg-brand-50/60 dark:hover:bg-slate-800 ${isSelected ? 'ring-2 ring-inset ring-brand-500 bg-brand-50/50 dark:bg-slate-800' : ''}`}
                >
                  <span
                    className={`w-6 h-6 rounded-full flex items-center justify-center text-xs flex-shrink-0 ${
                      day.is_today
                        ? 'bg-brand-600 text-white font-bold'
                        : day.in_month
                          ? 'font-semibold text-slate-700 dark:text-slate-200'
                          : 'text-slate-300 dark:text-slate-600'
                    }`}
                  >
                    {day.day_number}
                  </span>
                  <span className="flex flex-col gap-1 w-full min-w-0">
                    {shown.map((chip) => (
                      <span
                        key={chip.key}
                        className={`flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-medium min-w-0 ${chip.cls}`}
                        title={day[chip.key]}
                      >
                        <span className="w-1.5 h-1.5 rounded-full bg-current flex-shrink-0" />
                        <span className="truncate">{chip.text}</span>
                      </span>
                    ))}
                    {moreCount > 0 && <span className="text-[10px] font-medium text-slate-400 px-1">+{moreCount} more</span>}
                  </span>
                  {revenueLabel && (
                    <span className="mt-auto text-[10px] text-slate-400 dark:text-slate-500 tabular-nums">Rev {revenueLabel}</span>
                  )}
                </button>
              );
            })}
          </div>
        ))}
      </div>

      <div className="flex items-center gap-4 flex-wrap px-4 sm:px-5 py-2.5 border-t border-slate-200 dark:border-slate-800 text-[10px] text-slate-400">
        {CHIP_GROUPS.map((g) => (
          <span key={g.key} className="flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full border ${g.cls}`} />
            {g.label}
          </span>
        ))}
        <span className="ml-auto hidden sm:inline">Rev = revenue submitted that day</span>
      </div>
    </div>
  );
}
