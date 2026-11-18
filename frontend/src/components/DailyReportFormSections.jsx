import StepsCounter from './StepsCounter';
import YesNoToggle from './YesNoToggle';
import PercentProgress from './PercentProgress';
import { Field, ChipGroup, inputClass } from './FormFields';
import { GLASS } from '../utils/glassTheme';
import { viewUploadUrl } from '../utils/safeUrl';

/** The daily-report form sections (F-M1) — previously duplicated in full
 * between CeoDashboardPage and HeadOfficePage. The CEO form is the
 * superset; the HO form is the same sections minus the CEO-only fields,
 * switched on by flags so both pages render identically to before:
 * - Activity: `showMeetingsCount`, `showKeyAchievement`, `showProblems`,
 *   `showHelp` (+ `helpResponse`), `showPercents` (HO keeps the exercise
 *   % in Activity; CEO keeps them in Health).
 * - Health: `showChips`, `showWeight`, `showSick` (CEO only), `showPercents`.
 * All labels are wired to their inputs via `Field` (htmlFor/id, F-m5).
 * `sectionIcon` is the small header SVG path, kept per-section as before.
 */

function SectionHeader({ icon, title, badge }) {
  return (
    <div className="px-6 py-4 border-b border-slate-200/70 dark:border-slate-800 flex items-center gap-2.5">
      <div className="w-8 h-8 rounded-lg bg-brand-500/10 flex items-center justify-center">
        <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 text-brand-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
          <path strokeLinecap="round" strokeLinejoin="round" d={icon} />
        </svg>
      </div>
      <h2 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight">{title}</h2>
      {badge}
    </div>
  );
}

function MoneyField({ label, fieldKey, form, updateField, optional, min }) {
  return (
    <Field label={label} optional={optional}>
      {(id) => (
        <div className="relative">
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-sm font-semibold">K</span>
          <input
            id={id}
            type="number"
            step="0.01"
            min={min}
            value={form[fieldKey]}
            onChange={(e) => updateField(fieldKey, e.target.value)}
            className={`${inputClass} pl-7`}
            placeholder="0.00"
          />
        </div>
      )}
    </Field>
  );
}

export function FinancialSection({ form, updateField }) {
  return (
    <section className={`${GLASS} overflow-hidden`}>
      <SectionHeader
        icon="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
        title="Business Performance"
        badge={<span className="ml-auto text-xs font-semibold px-2.5 py-1 rounded-full bg-red-500/10 border border-red-500/20 text-red-600">Mandatory</span>}
      />
      <div className="p-6 grid sm:grid-cols-2 lg:grid-cols-5 gap-5">
        <MoneyField label="Revenue" fieldKey="revenue" form={form} updateField={updateField} min="0" />
        <MoneyField label="Cash Flow" fieldKey="cash_flow" form={form} updateField={updateField} />
        <MoneyField label="Receivable" fieldKey="receivable" form={form} updateField={updateField} min="0" />
        <MoneyField label="Payable" fieldKey="payable" form={form} updateField={updateField} min="0" />
        <MoneyField label="Other Amount" fieldKey="other_amount" form={form} updateField={updateField} optional />
      </div>
      <div className="px-6 pb-6">
        <Field label="Other" optional>
          {(id) => (
            <textarea
              id={id}
              rows={2}
              placeholder="Anything else about today's financials..."
              value={form.financial_other_notes}
              onChange={(e) => updateField('financial_other_notes', e.target.value)}
              className={inputClass}
            />
          )}
        </Field>
      </div>
    </section>
  );
}

function PercentsRow({ form, updateField }) {
  return (
    <div className="px-6 pb-6 grid sm:grid-cols-3 gap-5">
      <PercentProgress label="Gym %" value={form.gym_percent} onChange={(v) => updateField('gym_percent', v)} color="brand" />
      <PercentProgress label="Swimming %" value={form.swimming_percent} onChange={(v) => updateField('swimming_percent', v)} color="emerald" />
      <PercentProgress label="Cycle %" value={form.cycle_percent} onChange={(v) => updateField('cycle_percent', v)} color="amber" />
    </div>
  );
}

export function ActivitySection({
  form, updateField, data, showMeetingsCount = false, showKeyAchievement = false,
  showProblems = false, showHelp = false, showPercents = false,
  scheduleLabel = 'Next Day Personal Schedule', schedulePlaceholder = "Outline tomorrow's personal schedule...",
  attachmentUrl, onAttachmentChange,
}) {
  return (
    <section className={`${GLASS} overflow-hidden`}>
      <SectionHeader
        icon="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
        title="Daily Activities"
      />
      <div className="p-6 grid sm:grid-cols-2 gap-5">
        <div>
          <Field label="Meetings Attended">
            {(id) => (
              <textarea
                id={id}
                rows={4}
                placeholder="Describe key meetings attended today..."
                value={form.meetings_attended}
                onChange={(e) => updateField('meetings_attended', e.target.value)}
                className={inputClass}
              />
            )}
          </Field>
          {showMeetingsCount && (
            <div className="flex items-center gap-2 mt-2">
              <label htmlFor="meetings-count" className="text-xs font-medium text-slate-500">Meetings (count)</label>
              <input
                id="meetings-count"
                type="number"
                min="0"
                max="99"
                value={form.meetings_count}
                onChange={(e) => updateField('meetings_count', e.target.value)}
                placeholder="0"
                className="w-24 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-800 dark:text-slate-100 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
              />
            </div>
          )}
        </div>
        <Field label="Site Visits / Travel">
          {(id) => (
            <textarea
              id={id}
              rows={4}
              placeholder="Describe site visits or travel undertaken..."
              value={form.site_visits}
              onChange={(e) => updateField('site_visits', e.target.value)}
              className={inputClass}
            />
          )}
        </Field>
        <Field label="Key Decisions Made">
          {(id) => (
            <textarea
              id={id}
              rows={4}
              placeholder="Summarize key decisions made today..."
              value={form.key_decisions}
              onChange={(e) => updateField('key_decisions', e.target.value)}
              className={inputClass}
            />
          )}
        </Field>
        {showKeyAchievement && (
          <Field label="Key Achievement" optional>
            {(id) => (
              <textarea
                id={id}
                rows={4}
                placeholder="The headline result of the day — what actually got achieved..."
                value={form.key_achievement}
                onChange={(e) => updateField('key_achievement', e.target.value)}
                className={inputClass}
              />
            )}
          </Field>
        )}
        <Field label={scheduleLabel}>
          {(id) => (
            <textarea
              id={id}
              rows={4}
              placeholder={schedulePlaceholder}
              value={form.next_day_schedule}
              onChange={(e) => updateField('next_day_schedule', e.target.value)}
              className={inputClass}
            />
          )}
        </Field>
        {showProblems && (
          <Field label="Problems Today" optional>
            {(id) => (
              <textarea
                id={id}
                rows={4}
                placeholder="Blockers, issues, delays — anything that slowed the business down today..."
                value={form.problems_today}
                onChange={(e) => updateField('problems_today', e.target.value)}
                className={inputClass}
              />
            )}
          </Field>
        )}
        {showHelp && (
          <div>
            <Field label="Help Needed from HO / Chairman" optional>
              {(id) => (
                <textarea
                  id={id}
                  rows={4}
                  placeholder="What decision or support do you need from Head Office or the Chairman?"
                  value={form.help_needed}
                  onChange={(e) => updateField('help_needed', e.target.value)}
                  className="w-full rounded-lg border border-amber-300 bg-amber-50/60 text-slate-800 placeholder:text-slate-400 px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500 focus:border-transparent"
                />
              )}
            </Field>
            <p className="text-[11px] text-slate-400 mt-1.5">Filling this in flags your report to the Chairman &amp; Head Office as needing attention.</p>
          </div>
        )}
      </div>
      {showHelp && data?.form_activity?.help_resolved_at && (
        <div className="px-6 pb-6">
          <div className="rounded-lg bg-emerald-500/10 border border-emerald-500/25 px-4 py-3">
            <p className="text-[11px] font-bold uppercase tracking-[0.1em] text-emerald-700 mb-1">
              Response from HO / Chairman — resolved {data.form_activity.help_resolved_at}
              {data.form_activity.help_resolved_by_username ? ` by ${data.form_activity.help_resolved_by_username}` : ''}
            </p>
            <p className="text-sm text-slate-700 whitespace-pre-wrap">{data.form_activity.help_response || '—'}</p>
            <p className="text-[11px] text-slate-400 mt-1.5">Editing the help request above and resubmitting will re-open it for a new response.</p>
          </div>
        </div>
      )}
      {showPercents && <PercentsRow form={form} updateField={updateField} />}
      <div className="px-6 pb-6">
        <Field label="Other" optional>
          {(id) => (
            <textarea
              id={id}
              rows={2}
              placeholder="Anything else about today's activity..."
              value={form.activity_other_notes}
              onChange={(e) => updateField('activity_other_notes', e.target.value)}
              className={inputClass}
            />
          )}
        </Field>
      </div>
      <div className="px-6 pb-6">
        <label htmlFor="report-attachment" className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">
          Supporting Document <span className="text-slate-400 font-normal">(optional — minutes, photos, receipts)</span>
        </label>
        <input
          id="report-attachment"
          type="file"
          onChange={onAttachmentChange}
          className="block w-full text-sm text-slate-500 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-semibold file:bg-brand-500/10 file:text-brand-600 hover:file:bg-brand-500/20"
        />
        {attachmentUrl && (
          <p className="mt-2 text-xs text-slate-500">
            Current file:{' '}
            <a href={attachmentUrl} target="_blank" rel="noreferrer" className="text-brand-600 dark:text-brand-400 hover:text-brand-700 hover:underline">
              View attachment
            </a>
          </p>
        )}
      </div>
    </section>
  );
}

export function HealthSection({ form, updateField, showChips = false, showWeight = false, showSick = false, showPercents = false }) {
  return (
    <section className={`${GLASS} overflow-hidden`}>
      <SectionHeader
        icon="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z"
        title="Health and Fitness"
      />
      <div className="p-6 grid sm:grid-cols-2 lg:grid-cols-4 gap-8">
        <StepsCounter value={form.daily_steps} onChange={(v) => updateField('daily_steps', v)} />
        <Field label="Heart Rate" optional>
          {(id) => (
            <div className="relative">
              <input
                id={id}
                type="number"
                min="0"
                value={form.heart_rate}
                onChange={(e) => updateField('heart_rate', e.target.value)}
                placeholder="e.g. 72"
                className={`${inputClass} pr-14`}
              />
              <span className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 text-xs font-medium">bpm</span>
            </div>
          )}
        </Field>
        <Field label="Sleep Time" optional>
          {(id) => (
            <div className="relative">
              <input
                id={id}
                type="number"
                min="0"
                max="24"
                step="0.5"
                value={form.sleep_hours}
                onChange={(e) => updateField('sleep_hours', e.target.value)}
                placeholder="e.g. 7.5"
                className={`${inputClass} pr-12`}
              />
              <span className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 text-xs font-medium">hrs</span>
            </div>
          )}
        </Field>
        <div>
          <span className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-1.5">
            Blood Pressure <span className="text-slate-400 font-normal">(mmHg, optional)</span>
          </span>
          <div className="flex items-center gap-2">
            <input
              id="bp-systolic"
              aria-label="Blood pressure systolic"
              type="number"
              min="0"
              value={form.bp_systolic}
              onChange={(e) => updateField('bp_systolic', e.target.value)}
              placeholder="120"
              className={inputClass}
            />
            <span className="text-slate-400 text-sm font-medium flex-shrink-0">/</span>
            <input
              id="bp-diastolic"
              aria-label="Blood pressure diastolic"
              type="number"
              min="0"
              value={form.bp_diastolic}
              onChange={(e) => updateField('bp_diastolic', e.target.value)}
              placeholder="80"
              className={inputClass}
            />
          </div>
        </div>
      </div>
      {(showChips || showWeight || showSick) && (
        <div className="px-6 pb-6 grid sm:grid-cols-2 gap-5">
          {showChips && (
            <>
              <ChipGroup label="Stress Level" options={['Low', 'Medium', 'High']} value={form.stress_level} onChange={(v) => updateField('stress_level', v)} />
              <ChipGroup label="Energy / Mood" options={['Energetic', 'Normal', 'Drained']} value={form.energy_mood} onChange={(v) => updateField('energy_mood', v)} />
            </>
          )}
          {showWeight && (
            <Field label="Weight" optional>
              {(id) => (
                <div className="relative">
                  <input
                    id={id}
                    type="number"
                    min="20"
                    max="400"
                    step="0.1"
                    value={form.weight_kg}
                    onChange={(e) => updateField('weight_kg', e.target.value)}
                    placeholder="e.g. 68.5"
                    className={`${inputClass} pr-12`}
                  />
                  <span className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 text-xs font-medium">kg</span>
                </div>
              )}
            </Field>
          )}
          {showSick && <YesNoToggle label="Feeling Sick Today" value={form.is_sick} onChange={(v) => updateField('is_sick', v)} />}
        </div>
      )}
      {showPercents && <PercentsRow form={form} updateField={updateField} />}
      <div className="px-6 pb-6">
        <Field label="Other" optional>
          {(id) => (
            <textarea
              id={id}
              rows={2}
              placeholder="Anything else about today's health/well-being..."
              value={form.health_other_notes}
              onChange={(e) => updateField('health_other_notes', e.target.value)}
              className={inputClass}
            />
          )}
        </Field>
      </div>
    </section>
  );
}

export function SocialSection({ form, updateField }) {
  return (
    <section className={`${GLASS} overflow-hidden`}>
      <SectionHeader
        icon="M17 20h5v-2a4 4 0 00-3-3.87M9 20H4v-2a4 4 0 013-3.87m6-2.13a4 4 0 10-4-4 4 4 0 004 4zm6 0a4 4 0 10-4-4"
        title="Social Activity"
      />
      <div className="p-6 grid sm:grid-cols-3 gap-5">
        <YesNoToggle label="Event Attended" value={form.event_flag} onChange={(v) => updateField('event_flag', v)} />
        <YesNoToggle label="Meeting Attended" value={form.meeting_flag} onChange={(v) => updateField('meeting_flag', v)} />
        <YesNoToggle label="Golf" value={form.golf_flag} onChange={(v) => updateField('golf_flag', v)} />
      </div>
      <div className="px-6 pb-6">
        <Field label="Other" optional>
          {(id) => (
            <textarea
              id={id}
              rows={2}
              placeholder="Anything else about today's social engagement..."
              value={form.social_other_notes}
              onChange={(e) => updateField('social_other_notes', e.target.value)}
              className={inputClass}
            />
          )}
        </Field>
      </div>
    </section>
  );
}
