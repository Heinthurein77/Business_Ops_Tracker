import { safeUrl } from '../utils/safeUrl';

function initials(name) {
  if (!name) return '?';
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  return (parts[0][0] + (parts[1]?.[0] || '')).toUpperCase();
}

const PROFILE_STAT_ICON = {
  phone: 'M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z',
  mail: 'M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z',
  calendar: 'M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z',
  pin: 'M17.657 16.657L13.414 20.9a2 2 0 01-2.828 0l-4.243-4.243a8 8 0 1111.314 0zM15 11a3 3 0 11-6 0 3 3 0 016 0z',
};

function ProfileStat({ icon, label, value, title }) {
  return (
    <div className="flex items-center gap-2 min-w-0">
      <span className="w-7 h-7 rounded-lg bg-brand-500/10 text-brand-600 dark:text-brand-400 flex items-center justify-center flex-shrink-0">
        <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
          <path strokeLinecap="round" strokeLinejoin="round" d={PROFILE_STAT_ICON[icon]} />
        </svg>
      </span>
      <div className="min-w-0">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500 leading-tight">{label}</p>
        <p className="text-xs font-semibold text-slate-700 dark:text-slate-300 truncate max-w-[11rem]" title={title || value}>{value}</p>
      </div>
    </div>
  );
}

/** Shared CEO profile summary card — photo/initials, name + title badge,
 * contact chips (phone/email/age/address), and an "About the Business" strip.
 * Used identically by the Executive Dashboard (viewing any BU's CEO) and the
 * CEO's own dashboard (viewing themselves), so both read as one design. */
export default function CeoProfileCard({ ceoProfile, subtitle, glassClass }) {
  if (!ceoProfile) return null;
  return (
    <section className={`${glassClass} overflow-hidden`}>
      <div className="px-5 sm:px-6 py-5 flex flex-col lg:flex-row lg:items-center gap-5">
        <div className="flex items-center gap-4 min-w-0 lg:flex-1">
          {safeUrl(ceoProfile.profile_pic_url) ? (
            <img src={safeUrl(ceoProfile.profile_pic_url)} alt="" className="w-16 h-16 rounded-xl object-cover border-2 border-white dark:border-slate-700 shadow-sm ring-1 ring-slate-200 dark:ring-slate-700 flex-shrink-0" />
          ) : (
            <div className="w-16 h-16 rounded-xl bg-gradient-to-br from-brand-500 to-indigo-500 flex items-center justify-center flex-shrink-0 shadow-sm">
              <span className="text-lg font-bold text-white tracking-wide">{initials(ceoProfile.full_name)}</span>
            </div>
          )}
          <div className="min-w-0">
            <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">CEO Profile</p>
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="font-bold text-slate-900 dark:text-slate-100 text-xl tracking-tight tracking-tight truncate">{ceoProfile.full_name}</h2>
              {ceoProfile.is_profile_completed ? (
                ceoProfile.executive_title && (
                  <span className="text-[11px] font-medium text-brand-700 dark:text-brand-300 bg-brand-500/10 border border-brand-500/20 px-2.5 py-0.5 rounded-full whitespace-nowrap">{ceoProfile.executive_title}</span>
                )
              ) : (
                <span className="text-[10px] font-bold uppercase tracking-wide px-2.5 py-0.5 rounded-full border bg-amber-500/10 border-amber-500/20 dark:border-amber-500/30 text-amber-600 dark:text-amber-400 whitespace-nowrap">Profile Pending</span>
              )}
            </div>
            {subtitle && <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5 truncate">{subtitle}</p>}
          </div>
        </div>

        {ceoProfile.is_profile_completed && (
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:flex lg:items-center gap-x-6 gap-y-3.5 lg:pl-6 lg:border-l lg:border-slate-200 lg:dark:border-slate-700">
            <ProfileStat icon="phone" label="Phone" value={ceoProfile.phone_number || '—'} />
            <ProfileStat icon="mail" label="Email" value={ceoProfile.email || '—'} />
            <ProfileStat
              icon="calendar"
              label="Age"
              value={ceoProfile.age != null ? `${ceoProfile.age} yrs` : '—'}
              title={ceoProfile.date_of_birth || ''}
            />
            <ProfileStat icon="pin" label="Address" value={ceoProfile.address || '—'} />
          </div>
        )}
      </div>

      {ceoProfile.is_profile_completed && ceoProfile.business_details && (
        <div className="px-5 sm:px-6 pb-4 pt-0.5 -mt-1">
          <div className="rounded-xl border border-slate-100 dark:border-slate-800 bg-slate-50/70 dark:bg-slate-900 px-4 py-2.5">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500 mb-0.5">About the Business</p>
            <p className="text-xs text-slate-600 dark:text-slate-400 line-clamp-2">{ceoProfile.business_details}</p>
          </div>
        </div>
      )}
    </section>
  );
}
