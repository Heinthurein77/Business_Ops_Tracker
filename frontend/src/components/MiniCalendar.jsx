import { useEffect, useRef, useState } from 'react';
import { money } from '../utils/quadrantMetrics';

const WEEKDAY_LABELS = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];

const FINANCIAL_BADGE_CLASS = {
  red: 'bg-red-500/10 border-red-500/20 text-red-600',
  yellow: 'bg-amber-500/10 border-amber-500/20 text-amber-600',
  green: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600',
  grey: 'bg-slate-100 border-slate-200 text-slate-500',
};

export default function MiniCalendar({ calendarWeeks, monthLabel, prevMonth, nextMonth, onMonthChange }) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState(null);
  const popoverRef = useRef(null);

  useEffect(() => {
    function handleOutsideClick(e) {
      if (popoverRef.current && !popoverRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handleOutsideClick);
    return () => document.removeEventListener('mousedown', handleOutsideClick);
  }, []);

  return (
    <div className="relative inline-block" ref={popoverRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="text-slate-400 hover:text-brand-600 transition-colors"
        title="Activity calendar"
      >
        <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
          <path strokeLinecap="round" strokeLinejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
        </svg>
      </button>

      {open && (
        <div className="absolute z-30 right-0 mt-2 w-[min(18rem,90vw)] bg-white border border-slate-200 rounded-2xl shadow-2xl shadow-slate-900/10 p-4">
          <div className="flex items-center justify-center gap-1 mb-3">
            <button
              type="button"
              onClick={() => onMonthChange(prevMonth)}
              className="w-7 h-7 flex items-center justify-center rounded-full hover:bg-slate-100 text-slate-500 transition-colors"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
              </svg>
            </button>
            <span className="text-sm font-semibold text-slate-900 w-32 text-center">{monthLabel}</span>
            <button
              type="button"
              onClick={() => onMonthChange(nextMonth)}
              className="w-7 h-7 flex items-center justify-center rounded-full hover:bg-slate-100 text-slate-500 transition-colors"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
              </svg>
            </button>
          </div>

          <div className="grid grid-cols-7 text-center text-[10px] font-medium text-slate-400 mb-1">
            {WEEKDAY_LABELS.map((label, i) => (
              <div key={i}>{label}</div>
            ))}
          </div>

          <div className="space-y-0.5">
            {calendarWeeks.map((week, wi) => (
              <div className="grid grid-cols-7" key={wi}>
                {week.map((day) => {
                  const missingDay = day.in_month && !day.is_future && !day.has_report;
                  const isSelected = selected && selected.date_iso === day.date_iso;
                  const baseClasses = 'w-7 h-7 rounded-full text-[11px] flex items-center justify-center relative transition-colors';
                  let stateClasses;
                  if (day.has_report) {
                    stateClasses = `cursor-pointer ${day.is_today ? 'bg-brand-500/10 text-brand-700 font-bold' : 'text-slate-700'}`;
                  } else {
                    stateClasses = `cursor-default ${day.is_today ? 'bg-brand-500/10 text-brand-700 font-bold' : 'text-slate-300'}`;
                  }
                  return (
                    <div className="flex justify-center py-0.5" key={day.date_iso}>
                      <button
                        type="button"
                        disabled={!day.has_report}
                        tabIndex={day.has_report ? 0 : -1}
                        onClick={() => day.has_report && setSelected(day)}
                        className={`${baseClasses} ${stateClasses} ${day.in_month ? '' : 'opacity-30'} ${isSelected ? 'mini-cal-day-selected' : ''}`}
                      >
                        {day.day_number}
                        {day.has_report && <span className="absolute bottom-0.5 w-1 h-1 rounded-full bg-emerald-500" />}
                        {missingDay && <span className="absolute bottom-0.5 w-1 h-1 rounded-full bg-red-400" />}
                      </button>
                    </div>
                  );
                })}
              </div>
            ))}
          </div>

          {selected && (
            <div className="mt-3 pt-3 border-t border-slate-100 text-[11px] text-slate-500 space-y-3 max-h-64 overflow-y-auto">
              <div className="flex items-center justify-between">
                <p className="font-semibold text-slate-900">{selected.date_display}</p>
                {selected.financial_status && (
                  <span className={`text-[9px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded-full border ${FINANCIAL_BADGE_CLASS[selected.financial_status]}`}>
                    {selected.financial_status}
                  </span>
                )}
              </div>

              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400 mb-1">Business Performance</p>
                {selected.revenue != null ? (
                  <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-slate-700">
                    <span>Revenue: {money(selected.revenue)}</span>
                    <span>Cash Flow: {money(selected.cash_flow)}</span>
                    <span>Receivable: {money(selected.receivable)}</span>
                    <span>Payable: {money(selected.payable)}</span>
                  </div>
                ) : (
                  <p className="text-slate-400 italic">No financial data</p>
                )}
              </div>

              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400 mb-1">Daily Activities</p>
                <div className="grid grid-cols-3 gap-x-2 text-slate-700 mb-1">
                  <span>Gym: {selected.gym_percent != null ? `${selected.gym_percent}%` : '—'}</span>
                  <span>Swim: {selected.swimming_percent != null ? `${selected.swimming_percent}%` : '—'}</span>
                  <span>Cycle: {selected.cycle_percent != null ? `${selected.cycle_percent}%` : '—'}</span>
                </div>
                <p className="text-[10px] text-slate-400">Meetings</p>
                <p className="whitespace-pre-wrap text-slate-700">{selected.meetings_attended || '—'}</p>
                <p className="text-[10px] text-slate-400 mt-1">Site Visits / Travel</p>
                <p className="whitespace-pre-wrap text-slate-700">{selected.site_visits || '—'}</p>
                <p className="text-[10px] text-slate-400 mt-1">Key Decisions Made</p>
                <p className="whitespace-pre-wrap text-slate-700">{selected.key_decisions || '—'}</p>
                <p className="text-[10px] text-slate-400 mt-1">Next Day Schedule</p>
                <p className="whitespace-pre-wrap text-slate-700">{selected.next_day_schedule || '—'}</p>
              </div>

              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400 mb-1">Health and Fitness</p>
                {selected.daily_steps != null || selected.heart_rate != null || selected.physical_health ? (
                  <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-slate-700">
                    <span>Steps: {selected.daily_steps != null ? selected.daily_steps.toLocaleString() : '—'}</span>
                    <span>Heart Rate: {selected.heart_rate != null ? `${selected.heart_rate} bpm` : '—'}</span>
                    <span>Sleep: {selected.sleep_hours != null ? `${selected.sleep_hours} hrs` : '—'}</span>
                    <span>BP: {selected.bp_systolic != null && selected.bp_diastolic != null ? `${selected.bp_systolic}/${selected.bp_diastolic}` : '—'}</span>
                    <span>Mood: {selected.energy_mood || '—'}</span>
                    <span>Stress: {selected.stress_level || '—'}</span>
                  </div>
                ) : (
                  <p className="text-slate-400 italic">No health data</p>
                )}
              </div>

              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400 mb-1">Social Activity</p>
                {selected.event_flag != null ? (
                  <div className="flex gap-3 text-slate-700">
                    <span>Event: {selected.event_flag ? 'Yes' : 'No'}</span>
                    <span>Meeting: {selected.meeting_flag ? 'Yes' : 'No'}</span>
                    <span>Golf: {selected.golf_flag ? 'Yes' : 'No'}</span>
                  </div>
                ) : (
                  <p className="text-slate-400 italic">No social data</p>
                )}
              </div>
            </div>
          )}

          <div className="flex items-center gap-3 mt-3 pt-3 border-t border-slate-100 text-[10px] text-slate-400 flex-wrap">
            <span className="flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" /> Report submitted
            </span>
            <span className="flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-red-400" /> Missing
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
