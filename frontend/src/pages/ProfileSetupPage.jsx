import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth, resolveHomePath, roleHomePath } from '../context/AuthContext';
import { ApiError, api } from '../api/client';
import DatePicker from '../components/DatePicker';
import { safeUrl } from '../utils/safeUrl';

const TODAY_ISO = new Date().toISOString().slice(0, 10);

const emptyForm = {
  full_name: '',
  executive_title: 'Chief Executive Officer',
  phone_number: '',
  email: '',
  date_of_birth: '',
  address: '',
  business_details: '',
};

const MAX_PHOTO_BYTES = 5 * 1024 * 1024;
const ALLOWED_PHOTO_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];

export default function ProfileSetupPage() {
  const { user, refresh } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState(emptyForm);
  const [profile, setProfile] = useState(null);
  const [editing, setEditing] = useState(false);
  const [managedBusinessIds, setManagedBusinessIds] = useState([]);
  const [existingPhotoUrl, setExistingPhotoUrl] = useState(null);
  const [photoFile, setPhotoFile] = useState(null);
  const [photoPreview, setPhotoPreview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [photoWarning, setPhotoWarning] = useState('');
  const [submitting, setSubmitting] = useState(false);

  function applyProfile(data) {
    setProfile(data);
    setForm({
      full_name: data.full_name || '',
      executive_title: data.executive_title || 'Chief Executive Officer',
      phone_number: data.phone_number || '',
      email: data.email || '',
      date_of_birth: data.date_of_birth || '',
      address: data.address || '',
      business_details: data.business_details || '',
    });
    setManagedBusinessIds(data.managed_business_ids || []);
    setExistingPhotoUrl(data.profile_pic_url || null);
    setPhotoFile(null);
    setPhotoPreview(null);
    // Mandatory first-time setup jumps straight into the form; a CEO
    // revisiting an already-complete profile lands on the read-only view.
    setEditing(!data.is_profile_completed);
  }

  useEffect(() => {
    api
      .getCeoProfile()
      .then(applyProfile)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  // Revoke the object URL when it's replaced/unmounted so it doesn't leak.
  useEffect(() => {
    return () => {
      if (photoPreview) URL.revokeObjectURL(photoPreview);
    };
  }, [photoPreview]);

  function updateField(key, value) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  function handlePhotoChange(e) {
    const file = e.target.files?.[0] || null;
    setError('');
    if (!file) {
      setPhotoFile(null);
      setPhotoPreview(null);
      return;
    }
    if (!ALLOWED_PHOTO_TYPES.includes(file.type)) {
      setError('Photo must be PNG, JPEG, GIF, or WEBP.');
      e.target.value = '';
      return;
    }
    if (file.size > MAX_PHOTO_BYTES) {
      setError('Photo must be 5 MB or smaller.');
      e.target.value = '';
      return;
    }
    setPhotoFile(file);
    setPhotoPreview(URL.createObjectURL(file));
  }

  function startEditing() {
    setError('');
    setEditing(true);
  }

  function cancelEditing() {
    if (profile) applyProfile(profile);
    setError('');
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError('');
    setPhotoWarning('');

    if (!form.full_name.trim() || !form.executive_title.trim() || !form.phone_number.trim() || !form.date_of_birth || !form.address.trim()) {
      setError('Full Name, Title, Phone Number, Date of Birth, and Address are all required.');
      return;
    }

    const formData = new FormData();
    formData.append('full_name', form.full_name.trim());
    formData.append('executive_title', form.executive_title.trim());
    formData.append('phone_number', form.phone_number.trim());
    if (form.email.trim()) formData.append('email', form.email.trim());
    formData.append('date_of_birth', form.date_of_birth);
    formData.append('address', form.address.trim());
    if (form.business_details.trim()) formData.append('business_details', form.business_details.trim());
    if (photoFile) formData.append('profile_photo', photoFile);

    setSubmitting(true);
    try {
      const result = await api.setCeoProfile(formData);
      const refreshedUser = await refresh();
      const wasAlreadyCompleted = profile?.is_profile_completed;
      const updated = await api.getCeoProfile();
      applyProfile(updated);
      if (result.photo_warning) {
        // The rest of the profile saved fine — only the photo failed — so
        // stay on this page and show why, rather than silently navigating
        // away as if the photo had been saved too.
        setPhotoWarning(result.photo_warning);
      } else if (!wasAlreadyCompleted) {
        navigate(resolveHomePath(refreshedUser));
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.');
    } finally {
      setSubmitting(false);
    }
  }

  const isMandatorySetup = profile && !profile.is_profile_completed;

  return (
    <div className="min-h-screen bg-slate-950 flex items-center justify-center p-6">
      <div className="w-full max-w-2xl">
        <div className="bg-slate-900 border border-slate-800 rounded-xl shadow-2xl p-8">
          <div className="flex items-center justify-between gap-3 mb-6">
            <div className="flex items-center gap-3 min-w-0">
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-brand-500 to-indigo-400 flex items-center justify-center flex-shrink-0">
                <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                </svg>
              </div>
              <div className="min-w-0">
                <h1 className="text-white font-bold text-lg tracking-tight">
                  {isMandatorySetup ? 'Complete Your CEO Profile' : editing ? 'Edit Your Profile' : 'My Profile'}
                </h1>
                <p className="text-slate-500 text-xs truncate">
                  Signed in as <span className="text-slate-300 font-medium">{user?.username}</span>
                  {managedBusinessIds.length > 0 && <> &middot; {managedBusinessIds.join(', ')}</>}
                </p>
              </div>
            </div>
            {!isMandatorySetup && (
              <Link to={roleHomePath(user?.role)} className="text-xs font-semibold text-slate-400 hover:text-slate-200 flex-shrink-0">
                &larr; Dashboard
              </Link>
            )}
          </div>

          <p className="text-sm text-slate-400 leading-relaxed mb-6">
            {isMandatorySetup
              ? 'This is required once before you can access your dashboard. Head Office reviews this information, so please keep it accurate.'
              : 'Head Office reviews this information — keep it up to date.'}
          </p>

          {loading ? (
            <div className="flex justify-center py-10">
              <div className="w-6 h-6 border-2 border-slate-700 border-t-brand-400 rounded-full animate-spin" />
            </div>
          ) : (
            <>
              {error && <div className="mb-5 rounded-lg bg-red-500/10 border border-red-500/30 px-4 py-3 text-sm text-red-400">{error}</div>}
              {photoWarning && (
                <div className="mb-5 rounded-lg bg-amber-500/10 border border-amber-500/30 px-4 py-3 text-sm text-amber-300 flex items-start gap-2">
                  <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                  </svg>
                  <span>{photoWarning}</span>
                </div>
              )}

              {!editing && profile ? (
                <div className="space-y-6">
                  <div className="flex items-center gap-4">
                    {safeUrl(profile.profile_pic_url) ? (
                      <img src={safeUrl(profile.profile_pic_url)} alt="" className="w-20 h-20 rounded-full object-cover border border-slate-700 flex-shrink-0" />
                    ) : (
                      <div className="w-20 h-20 rounded-full bg-slate-800 border border-slate-700 flex items-center justify-center flex-shrink-0">
                        <svg xmlns="http://www.w3.org/2000/svg" className="w-8 h-8 text-slate-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                        </svg>
                      </div>
                    )}
                    <div className="min-w-0">
                      <p className="text-lg font-bold text-white truncate">{profile.full_name}</p>
                      <p className="text-sm text-slate-400 truncate">{profile.executive_title}</p>
                    </div>
                  </div>

                  <div className="grid sm:grid-cols-2 gap-5 text-sm">
                    <div>
                      <p className="text-xs text-slate-500 mb-1">Phone Number</p>
                      <p className="text-slate-200 font-medium">{profile.phone_number || '—'}</p>
                    </div>
                    <div>
                      <p className="text-xs text-slate-500 mb-1">Email</p>
                      <p className="text-slate-200 font-medium">{profile.email || '—'}</p>
                    </div>
                    <div>
                      <p className="text-xs text-slate-500 mb-1">Date of Birth</p>
                      <p className="text-slate-200 font-medium">
                        {profile.date_of_birth || '—'}
                        {profile.age != null && <span className="text-slate-500 font-normal"> &middot; {profile.age} years old</span>}
                      </p>
                    </div>
                    <div className="sm:col-span-2">
                      <p className="text-xs text-slate-500 mb-1">Address</p>
                      <p className="text-slate-200 font-medium whitespace-pre-wrap">{profile.address || '—'}</p>
                    </div>
                    <div>
                      <p className="text-xs text-slate-500 mb-1">Business Unit</p>
                      <p className="text-slate-200 font-medium">{managedBusinessIds.join(', ') || '—'}</p>
                    </div>
                  </div>

                  <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
                    <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">About the Business</p>
                    <p className="text-sm text-slate-300 whitespace-pre-wrap">{profile.business_details || '—'}</p>
                  </div>

                  <button
                    type="button"
                    onClick={startEditing}
                    className="w-full rounded-lg bg-gradient-to-r from-brand-600 to-indigo-600 hover:from-brand-700 hover:to-indigo-700 text-white font-semibold py-2.5 text-sm shadow-lg shadow-brand-600/30 transition-all"
                  >
                    Edit Profile
                  </button>
                </div>
              ) : (
                <form onSubmit={handleSubmit} className="space-y-5">
                  <div className="grid sm:grid-cols-2 gap-5">
                    <div>
                      <label className="block text-sm font-medium text-slate-300 mb-1.5">Full Name</label>
                      <input
                        type="text"
                        required
                        placeholder="e.g. Daw Hnin Thazin"
                        value={form.full_name}
                        onChange={(e) => updateField('full_name', e.target.value)}
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 text-white px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent placeholder:text-slate-600"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-slate-300 mb-1.5">Executive Title</label>
                      <input
                        type="text"
                        required
                        placeholder="Chief Executive Officer"
                        value={form.executive_title}
                        onChange={(e) => updateField('executive_title', e.target.value)}
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 text-white px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent placeholder:text-slate-600"
                      />
                    </div>
                  </div>

                  <div className="grid sm:grid-cols-2 gap-5">
                    <div>
                      <label className="block text-sm font-medium text-slate-300 mb-1.5">Phone Number</label>
                      <input
                        type="tel"
                        required
                        placeholder="e.g. 09-xxxxxxxxx"
                        value={form.phone_number}
                        onChange={(e) => updateField('phone_number', e.target.value)}
                        className="w-full rounded-lg border border-slate-700 bg-slate-950 text-white px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent placeholder:text-slate-600"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-slate-300 mb-1.5">Date of Birth</label>
                      <DatePicker
                        variant="dark"
                        value={form.date_of_birth}
                        onChange={(iso) => updateField('date_of_birth', iso || '')}
                        max={TODAY_ISO}
                        placeholder="Select date of birth"
                      />
                    </div>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-slate-300 mb-1.5">
                      Email <span className="text-slate-500 font-normal">(optional)</span>
                    </label>
                    <input
                      type="email"
                      placeholder="e.g. ceo@example.com"
                      value={form.email}
                      onChange={(e) => updateField('email', e.target.value)}
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 text-white px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent placeholder:text-slate-600"
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-slate-300 mb-1.5">Address</label>
                    <textarea
                      required
                      rows={3}
                      placeholder="Home or office address"
                      value={form.address}
                      onChange={(e) => updateField('address', e.target.value)}
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 text-white px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent placeholder:text-slate-600 resize-none"
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-slate-300 mb-1.5">
                      About the Business <span className="text-slate-500 font-normal">(optional)</span>
                    </label>
                    <textarea
                      rows={4}
                      placeholder="Briefly describe this business unit — what it does, its market, anything Head Office should know…"
                      value={form.business_details}
                      onChange={(e) => updateField('business_details', e.target.value)}
                      maxLength={4000}
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 text-white px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent placeholder:text-slate-600 resize-none"
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-slate-300 mb-1.5">
                      Profile Photo <span className="text-slate-500 font-normal">(optional)</span>
                    </label>
                    <div className="flex items-center gap-4">
                      {photoPreview || safeUrl(existingPhotoUrl) ? (
                        <img
                          src={photoPreview || safeUrl(existingPhotoUrl)}
                          alt="Profile preview"
                          className="w-16 h-16 rounded-full object-cover border border-slate-700 flex-shrink-0"
                        />
                      ) : (
                        <div className="w-16 h-16 rounded-full bg-slate-800 border border-slate-700 flex items-center justify-center flex-shrink-0">
                          <svg xmlns="http://www.w3.org/2000/svg" className="w-6 h-6 text-slate-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                          </svg>
                        </div>
                      )}
                      <div className="flex-1 min-w-0">
                        <label className="inline-flex items-center gap-2 text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-slate-200 px-3.5 py-2 rounded-lg transition-colors border border-slate-700 cursor-pointer">
                          <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                          </svg>
                          {photoFile ? 'Change photo' : existingPhotoUrl ? 'Replace photo' : 'Choose photo'}
                          <input type="file" accept="image/png,image/jpeg,image/gif,image/webp" onChange={handlePhotoChange} className="hidden" />
                        </label>
                        {photoFile && <p className="text-[11px] text-slate-500 mt-1.5 truncate">{photoFile.name}</p>}
                        <p className="text-[11px] text-slate-600 mt-1">PNG, JPEG, GIF, or WEBP — up to 5 MB</p>
                      </div>
                    </div>
                  </div>

                  <div className="flex gap-3">
                    {!isMandatorySetup && (
                      <button
                        type="button"
                        onClick={cancelEditing}
                        className="flex-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 font-semibold py-2.5 text-sm transition-colors border border-slate-700"
                      >
                        Cancel
                      </button>
                    )}
                    <button
                      type="submit"
                      disabled={submitting}
                      className="flex-1 rounded-lg bg-gradient-to-r from-brand-600 to-indigo-600 hover:from-brand-700 hover:to-indigo-700 text-white font-semibold py-2.5 text-sm shadow-lg shadow-brand-600/30 transition-all disabled:opacity-60"
                    >
                      {submitting ? 'Saving…' : isMandatorySetup ? 'Save Profile & Continue' : 'Save Changes'}
                    </button>
                  </div>
                </form>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
