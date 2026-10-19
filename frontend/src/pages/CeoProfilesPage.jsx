import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import Footer from '../components/Footer';
import { GLASS, PAGE_BG } from '../utils/glassTheme';
import { safeUrl } from '../utils/safeUrl';

export default function CeoProfilesPage() {
  const [data, setData] = useState(null);
  const [search, setSearch] = useState('');

  useEffect(() => {
    api.ceoProfiles().then(setData);
  }, []);

  const filtered = useMemo(() => {
    if (!data) return [];
    const query = search.trim().toLowerCase();
    if (!query) return data.ceo_profiles;
    return data.ceo_profiles.filter((p) =>
      `${p.username} ${p.full_name} ${p.email || ''} ${p.managed_business_ids.join(' ')} ${(p.managed_business_names || []).join(' ')}`.toLowerCase().includes(query)
    );
  }, [data, search]);

  if (!data) {
    return (
      <div className={`min-h-screen flex items-center justify-center ${PAGE_BG}`}>
        <div className="w-8 h-8 border-2 border-slate-200 dark:border-slate-700 border-t-brand-600 rounded-full animate-spin" />
      </div>
    );
  }

  const completedCount = data.ceo_profiles.filter((p) => p.is_profile_completed).length;

  return (
    <div className={`min-h-screen ${PAGE_BG} text-slate-800 dark:text-slate-200 antialiased flex flex-col lg:flex-row`}>
      <AdminSidebar />

      <main className="flex-1 min-w-0 px-4 sm:px-6 py-6 sm:py-8 space-y-6 fade-in">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Head Office Review</p>
            <h1 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight text-xl">CEO Profiles</h1>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">{completedCount}/{data.total} completed</p>
          </div>
          <div className="relative w-full sm:w-72">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 dark:text-slate-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-4.35-4.35M17 10a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by CEO or business unit…"
              className="w-full text-sm pl-9 pr-3 py-2.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200 placeholder:text-slate-400 dark:placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
            />
          </div>
        </div>

        <section className={`${GLASS} overflow-hidden`}>
          <p className="lg:hidden px-6 pt-4 text-[11px] font-medium text-slate-400 dark:text-slate-500 flex items-center gap-1">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5"><path strokeLinecap="round" strokeLinejoin="round" d="M13 5l7 7-7 7M5 5l7 7-7 7" /></svg>
            Swipe to see all columns
          </p>
          <div className="overflow-x-auto">
            <table className="w-full min-w-max text-sm">
              <thead className="bg-white dark:bg-slate-900 text-slate-500 dark:text-slate-400 text-xs uppercase tracking-wider">
                <tr>
                  <th className="px-6 py-3 text-left">Business Unit</th>
                  <th className="px-6 py-3 text-left">CEO</th>
                  <th className="px-6 py-3 text-left">Title</th>
                  <th className="px-6 py-3 text-left">Phone</th>
                  <th className="px-6 py-3 text-left">Email</th>
                  <th className="px-6 py-3 text-center">Age</th>
                  <th className="px-6 py-3 text-left">Date of Birth</th>
                  <th className="px-6 py-3 text-left">Address</th>
                  <th className="px-6 py-3 text-left">Business Details</th>
                  <th className="px-6 py-3 text-center">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {filtered.map((p) => (
                  <tr key={p.user_id} className="hover:bg-slate-50 dark:hover:bg-slate-900 transition-colors">
                    <td className="px-6 py-3.5">
                      <div className="flex items-center gap-2.5">
                        {safeUrl(p.profile_pic_url) ? (
                          <img src={safeUrl(p.profile_pic_url)} alt="" className="w-8 h-8 rounded-full object-cover border border-slate-200 dark:border-slate-700 flex-shrink-0" />
                        ) : (
                          <div className="w-8 h-8 rounded-full bg-brand-50 dark:bg-brand-500/10 border border-slate-200 dark:border-slate-700 flex items-center justify-center text-[10px] font-bold text-brand-500 dark:text-brand-400 flex-shrink-0">
                            {p.managed_business_ids[0]?.slice(-2) || '—'}
                          </div>
                        )}
                        <span className="font-semibold text-slate-800 dark:text-slate-200">{p.managed_business_names?.join(', ') || '—'}</span>
                      </div>
                    </td>
                    <td className="px-6 py-3.5">
                      <p className="font-medium text-slate-700 dark:text-slate-300">{p.full_name}</p>
                      <p className="text-[11px] text-slate-500 dark:text-slate-400">{p.username}</p>
                    </td>
                    <td className="px-6 py-3.5 text-slate-600 dark:text-slate-400">{p.executive_title || '—'}</td>
                    <td className="px-6 py-3.5 text-slate-600 dark:text-slate-400">{p.phone_number || '—'}</td>
                    <td className="px-6 py-3.5 text-slate-600 dark:text-slate-400">{p.email || '—'}</td>
                    <td className="px-6 py-3.5 text-center text-slate-600 dark:text-slate-400 tabular-nums">{p.age ?? '—'}</td>
                    <td className="px-6 py-3.5 text-slate-600 dark:text-slate-400 tabular-nums">{p.date_of_birth || '—'}</td>
                    <td className="px-6 py-3.5 text-slate-500 dark:text-slate-400 max-w-xs truncate" title={p.address || ''}>{p.address || '—'}</td>
                    <td className="px-6 py-3.5 text-slate-500 dark:text-slate-400 max-w-xs truncate" title={p.business_details || ''}>{p.business_details || '—'}</td>
                    <td className="px-6 py-3.5 text-center">
                      {p.is_profile_completed ? (
                        <span className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 dark:border-emerald-500/30 px-2.5 py-1 rounded-full">Complete</span>
                      ) : (
                        <span className="text-xs font-semibold text-amber-600 dark:text-amber-400 bg-amber-500/10 border border-amber-500/20 dark:border-amber-500/30 px-2.5 py-1 rounded-full">Pending</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {filtered.length === 0 && <p className="text-center text-sm text-slate-500 dark:text-slate-400 py-14">No matching CEO found.</p>}
        </section>
        <Footer />
      </main>
    </div>
  );
}
