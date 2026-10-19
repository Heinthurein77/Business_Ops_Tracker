import { Fragment, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import Footer from '../components/Footer';
import { GLASS, PAGE_BG } from '../utils/glassTheme';
import { safeUrl } from '../utils/safeUrl';

const STATUS_CARD_CLASS = {
  green: 'border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 border-l-4 border-l-emerald-500',
  yellow: 'border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 border-l-4 border-l-amber-500',
  red: 'border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 border-l-4 border-l-red-500',
  grey: 'border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 border-l-4 border-l-slate-300 dark:border-l-slate-600',
};
const STATUS_DOT = { green: 'bg-emerald-500', yellow: 'bg-amber-500', red: 'bg-red-500', grey: 'bg-slate-400' };
const STATUS_LABEL = { green: 'Active', yellow: 'Moderate', red: 'Low Activity', grey: 'No Data' };

function ActivityCard({ a }) {
  return (
    <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5 hover:border-brand-200 dark:hover:border-brand-500/30 hover:shadow-sm transition-all">
      <div className="flex items-center justify-between mb-3">
        <div>
          <p className="text-sm font-bold text-slate-900 dark:text-slate-100">{a.bu_name}</p>
          <p className="text-xs text-slate-500 dark:text-slate-400">{a.ceo_username}</p>
        </div>
        <span className="text-[11px] font-semibold text-brand-700 dark:text-brand-300 bg-brand-500/10 border border-brand-500/20 px-2 py-1 rounded-full">
          {a.submitted_at ? a.submitted_at.split(', ')[1] : ''}
        </span>
      </div>
      <dl className="space-y-2.5 text-xs">
        <div>
          <dt className="text-slate-500 dark:text-slate-400 font-semibold uppercase tracking-wide text-[10px] mb-0.5">Meetings Attended</dt>
          <dd className="text-slate-600 dark:text-slate-400">{a.meetings_attended || '—'}</dd>
        </div>
        <div>
          <dt className="text-slate-500 dark:text-slate-400 font-semibold uppercase tracking-wide text-[10px] mb-0.5">Site Visits / Travel</dt>
          <dd className="text-slate-600 dark:text-slate-400">{a.site_visits || '—'}</dd>
        </div>
        <div>
          <dt className="text-slate-500 dark:text-slate-400 font-semibold uppercase tracking-wide text-[10px] mb-0.5">Key Decisions Made</dt>
          <dd className="text-slate-600 dark:text-slate-400">{a.key_decisions || '—'}</dd>
        </div>
        <div>
          <dt className="text-slate-500 dark:text-slate-400 font-semibold uppercase tracking-wide text-[10px] mb-0.5">Next Day Schedule</dt>
          <dd className="text-slate-600 dark:text-slate-400">{a.next_day_schedule || '—'}</dd>
        </div>
        <div className="grid grid-cols-3 gap-2 pt-1">
          <div className="bg-slate-50 dark:bg-slate-900 border border-slate-100 dark:border-slate-800 rounded-lg px-2 py-1.5 text-center">
            <p className="text-slate-500 dark:text-slate-400 text-[10px] uppercase tracking-wide">Gym</p>
            <p className="font-bold text-slate-800 dark:text-slate-200">{a.gym_percent != null ? `${a.gym_percent}%` : '—'}</p>
          </div>
          <div className="bg-slate-50 dark:bg-slate-900 border border-slate-100 dark:border-slate-800 rounded-lg px-2 py-1.5 text-center">
            <p className="text-slate-500 dark:text-slate-400 text-[10px] uppercase tracking-wide">Swim</p>
            <p className="font-bold text-slate-800 dark:text-slate-200">{a.swimming_percent != null ? `${a.swimming_percent}%` : '—'}</p>
          </div>
          <div className="bg-slate-50 dark:bg-slate-900 border border-slate-100 dark:border-slate-800 rounded-lg px-2 py-1.5 text-center">
            <p className="text-slate-500 dark:text-slate-400 text-[10px] uppercase tracking-wide">Cycle</p>
            <p className="font-bold text-slate-800 dark:text-slate-200">{a.cycle_percent != null ? `${a.cycle_percent}%` : '—'}</p>
          </div>
        </div>
      </dl>
      {safeUrl(a.document_url) && (
        <a href={safeUrl(a.document_url)} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1.5 text-xs font-semibold text-brand-600 dark:text-brand-400 hover:text-brand-700 dark:hover:text-brand-300">
          View attachment
        </a>
      )}
    </div>
  );
}

export default function ActivityDashboardPage() {
  const [data, setData] = useState(null);
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState(null);
  const [dirSearch, setDirSearch] = useState('');
  const [dirFilter, setDirFilter] = useState('all');

  useEffect(() => {
    api.activityDashboard().then(setData);
  }, []);

  const filteredHistory = useMemo(() => {
    if (!data) return [];
    const query = search.trim().toLowerCase();
    if (!query) return data.activity_history;
    return data.activity_history.filter((a) =>
      `${a.bu_code} ${a.bu_name} ${a.ceo_username} ${a.meetings_attended} ${a.site_visits} ${a.key_decisions} ${a.next_day_schedule}`
        .toLowerCase()
        .includes(query)
    );
  }, [data, search]);

  const filteredDirectory = useMemo(() => {
    if (!data) return [];
    const query = dirSearch.trim().toLowerCase();
    return data.directory.filter((d) => {
      const matchesSearch = !query || `${d.bu_code} ${d.bu_name} ${d.ceo_username}`.toLowerCase().includes(query);
      const matchesFilter = dirFilter === 'all' || d.status === dirFilter;
      return matchesSearch && matchesFilter;
    });
  }, [data, dirSearch, dirFilter]);

  if (!data) {
    return (
      <div className={`min-h-screen flex items-center justify-center ${PAGE_BG}`}>
        <div className="w-8 h-8 border-2 border-slate-200 dark:border-slate-700 border-t-brand-600 rounded-full animate-spin" />
      </div>
    );
  }

  const filterBtnClass = (key) =>
    `text-xs font-semibold px-3.5 py-2 rounded-lg transition-colors ${dirFilter === key ? 'bg-brand-600 text-white' : 'bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-700 hover:text-slate-700 dark:hover:text-slate-300'}`;

  return (
    <div className={`min-h-screen ${PAGE_BG} text-slate-800 dark:text-slate-200 antialiased flex flex-col lg:flex-row`}>
      <AdminSidebar />

      <main className="flex-1 min-w-0 px-4 sm:px-6 py-6 sm:py-8 space-y-8 fade-in">
        <header className={`${GLASS} px-5 sm:px-6 py-4 flex items-center justify-between flex-wrap gap-4`}>
          <div>
            <h1 className="font-bold text-slate-900 dark:text-slate-100 text-lg">Daily Activities</h1>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">{data.today_display}</p>
          </div>
        </header>

        <section className={`${GLASS} overflow-hidden`}>
          <div className="p-6 border-b border-slate-200/70 dark:border-slate-800 flex items-center justify-between flex-wrap gap-4">
            <div>
              <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Executive Briefing</p>
              <h1 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight text-lg">Today's CEO Activity</h1>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Meetings, site visits, key decisions &amp; next-day schedules submitted today</p>
            </div>
            <span className={`text-xs font-bold px-3 py-1.5 rounded-full border ${data.submitted_count === data.total_business_units ? 'bg-emerald-500/10 border-emerald-500/20 dark:border-emerald-500/30 text-emerald-600 dark:text-emerald-400' : 'bg-amber-500/10 border-amber-500/20 dark:border-amber-500/30 text-amber-600 dark:text-amber-400'}`}>
              {data.submitted_count} of {data.total_business_units} business units reported
            </span>
          </div>
          <div className="p-6">
            {data.today_briefing.length > 0 ? (
              <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-5">
                {data.today_briefing.map((a, i) => (
                  <ActivityCard a={a} key={i} />
                ))}
              </div>
            ) : (
              <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900 px-5 py-12 text-center">
                <p className="text-sm font-semibold text-slate-600 dark:text-slate-400">No activity submitted yet today</p>
                <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">CEO activity reports will appear here as they come in.</p>
              </div>
            )}
          </div>
        </section>

        <section className={`${GLASS} overflow-hidden`}>
          <div className="p-6 border-b border-slate-200/70 dark:border-slate-800 flex items-center justify-between flex-wrap gap-4">
            <div>
              <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Full Directory</p>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight text-lg">CEO Activity Directory</h2>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Every business unit's latest recorded activity &middot; click a card for full history</p>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <input type="text" placeholder="Search BU, CEO..." value={dirSearch} onChange={(e) => setDirSearch(e.target.value)}
                className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-500 px-3.5 py-2 text-sm w-full sm:w-56 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent" />
              <button onClick={() => setDirFilter('all')} className={filterBtnClass('all')}>All</button>
              <button onClick={() => setDirFilter('green')} className={filterBtnClass('green')}>Active</button>
              <button onClick={() => setDirFilter('yellow')} className={filterBtnClass('yellow')}>Moderate</button>
              <button onClick={() => setDirFilter('red')} className={filterBtnClass('red')}>Low Activity</button>
            </div>
          </div>
          <div className="p-6">
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
              {filteredDirectory.map((d) => (
                <Link key={d.bu_id} to={`/admin/business-unit/${d.bu_id}`} className={`rounded-xl border p-4 hover:-translate-y-0.5 transition-transform ${STATUS_CARD_CLASS[d.status]}`}>
                  <div className="flex items-center justify-between mb-2.5">
                    <div>
                      <p className="text-sm font-bold text-slate-900 dark:text-slate-100">{d.bu_name}</p>
                      <p className="text-xs text-slate-500 dark:text-slate-400">{d.ceo_username}</p>
                    </div>
                    <span className={`w-2.5 h-2.5 rounded-full ${STATUS_DOT[d.status]}`} />
                  </div>
                  {d.reported_display ? (
                    <div className="space-y-1 text-xs">
                      <div className="flex justify-between"><span className="text-slate-500 dark:text-slate-400">Meetings</span><span className="font-semibold text-slate-800 dark:text-slate-200 truncate max-w-[9rem]" title={d.meetings_attended || ''}>{d.meetings_attended || '—'}</span></div>
                      <div className="flex justify-between"><span className="text-slate-500 dark:text-slate-400">Site Visits</span><span className="font-semibold text-slate-800 dark:text-slate-200 truncate max-w-[9rem]" title={d.site_visits || ''}>{d.site_visits || '—'}</span></div>
                      <div className="flex justify-between"><span className="text-slate-500 dark:text-slate-400">Fitness Avg</span><span className={`font-semibold ${d.status === 'red' ? 'text-red-600 dark:text-red-400' : d.status === 'yellow' ? 'text-amber-600 dark:text-amber-400' : d.status === 'green' ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-500 dark:text-slate-400'}`}>{STATUS_LABEL[d.status]}</span></div>
                      <div className="flex justify-between gap-2 pt-1">
                        <span className="text-slate-500 dark:text-slate-400 text-[10px]">Gym {d.gym_percent != null ? `${d.gym_percent}%` : '—'}</span>
                        <span className="text-slate-500 dark:text-slate-400 text-[10px]">Swim {d.swimming_percent != null ? `${d.swimming_percent}%` : '—'}</span>
                        <span className="text-slate-500 dark:text-slate-400 text-[10px]">Cycle {d.cycle_percent != null ? `${d.cycle_percent}%` : '—'}</span>
                      </div>
                    </div>
                  ) : (
                    <p className="text-xs text-slate-500 dark:text-slate-400 italic">No activity data submitted yet</p>
                  )}
                  {d.reported_display && <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-2.5">Reported {d.reported_display}</p>}
                </Link>
              ))}
            </div>
            {filteredDirectory.length === 0 && <p className="text-center text-sm text-slate-500 dark:text-slate-400 py-10">No business units match this filter.</p>}
          </div>
        </section>

        <section className={`${GLASS} overflow-hidden`}>
          <div className="p-6 border-b border-slate-200/70 dark:border-slate-800 flex items-center justify-between flex-wrap gap-4">
            <div>
              <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Audit Trail</p>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight text-lg">Daily Activities History</h2>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Last 300 submissions &middot; click a row to expand full detail</p>
            </div>
            <input
              type="text"
              placeholder="Search BU, CEO, or content..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-500 px-3.5 py-2 text-sm w-full sm:w-72 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
            />
          </div>
          <p className="lg:hidden px-6 pt-3 text-[11px] font-medium text-slate-400 dark:text-slate-500 flex items-center gap-1">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5"><path strokeLinecap="round" strokeLinejoin="round" d="M13 5l7 7-7 7M5 5l7 7-7 7" /></svg>
            Swipe to see all columns
          </p>
          <div className="overflow-x-auto max-h-[70vh] overflow-y-auto">
            <table className="w-full min-w-max text-sm">
              <thead className="sticky top-0 z-[5] bg-white dark:bg-slate-900 text-slate-500 dark:text-slate-400 text-xs uppercase tracking-wider">
                <tr>
                  <th className="px-6 py-3 text-left">Business Unit</th>
                  <th className="px-6 py-3 text-left">CEO</th>
                  <th className="px-6 py-3 text-left">Date</th>
                  <th className="px-6 py-3 text-left">Submitted At</th>
                  <th className="px-6 py-3 text-left">Meetings (excerpt)</th>
                  <th className="px-6 py-3 text-center">Detail</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {filteredHistory.map((a, i) => (
                  <Fragment key={i}>
                    <tr className="hover:bg-slate-50 dark:hover:bg-slate-900 transition-colors cursor-pointer" onClick={() => setExpanded(expanded === i ? null : i)}>
                      <td className="px-6 py-3.5">
                        <p className="font-semibold text-slate-800 dark:text-slate-200">{a.bu_name}</p>
                      </td>
                      <td className="px-6 py-3.5 text-slate-600 dark:text-slate-400">{a.ceo_username}</td>
                      <td className="px-6 py-3.5 text-slate-500 dark:text-slate-400">{a.report_date}</td>
                      <td className="px-6 py-3.5 text-slate-500 dark:text-slate-400">{a.submitted_at || '—'}</td>
                      <td className="px-6 py-3.5 text-slate-500 dark:text-slate-400 max-w-sm truncate">{a.meetings_attended || '—'}</td>
                      <td className="px-6 py-3.5 text-center">
                        <button type="button" className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:text-brand-700 dark:hover:text-brand-300">
                          {expanded === i ? 'Hide' : 'View'}
                        </button>
                      </td>
                    </tr>
                    {expanded === i && (
                      <tr className="fade-in">
                        <td colSpan={6} className="px-6 py-5 bg-slate-50 dark:bg-slate-900">
                          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4 text-xs">
                            <div>
                              <p className="text-slate-500 dark:text-slate-400 font-semibold uppercase tracking-wide text-[10px] mb-1">Meetings Attended</p>
                              <p className="text-slate-700 dark:text-slate-300">{a.meetings_attended || '—'}</p>
                            </div>
                            <div>
                              <p className="text-slate-500 dark:text-slate-400 font-semibold uppercase tracking-wide text-[10px] mb-1">Site Visits / Travel</p>
                              <p className="text-slate-700 dark:text-slate-300">{a.site_visits || '—'}</p>
                            </div>
                            <div>
                              <p className="text-slate-500 dark:text-slate-400 font-semibold uppercase tracking-wide text-[10px] mb-1">Key Decisions Made</p>
                              <p className="text-slate-700 dark:text-slate-300">{a.key_decisions || '—'}</p>
                            </div>
                            <div>
                              <p className="text-slate-500 dark:text-slate-400 font-semibold uppercase tracking-wide text-[10px] mb-1">Next Day Schedule</p>
                              <p className="text-slate-700 dark:text-slate-300">{a.next_day_schedule || '—'}</p>
                            </div>
                            <div>
                              <p className="text-slate-500 dark:text-slate-400 font-semibold uppercase tracking-wide text-[10px] mb-1">Gym / Swimming / Cycle</p>
                              <p className="text-slate-700 dark:text-slate-300">
                                {a.gym_percent != null ? `${a.gym_percent}%` : '—'} / {a.swimming_percent != null ? `${a.swimming_percent}%` : '—'} / {a.cycle_percent != null ? `${a.cycle_percent}%` : '—'}
                              </p>
                            </div>
                          </div>
                          {safeUrl(a.document_url) && (
                            <a href={safeUrl(a.document_url)} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1.5 text-xs font-semibold text-brand-600 dark:text-brand-400 hover:text-brand-700 dark:hover:text-brand-300">
                              View attachment
                            </a>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
            {filteredHistory.length === 0 && <p className="text-center text-sm text-slate-500 dark:text-slate-400 py-10">No activity logs match your search.</p>}
          </div>
        </section>
        <Footer />
      </main>
    </div>
  );
}
