import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import Footer from '../components/Footer';
import { GLASS, PAGE_BG } from '../utils/glassTheme';

const TABS = [
  { key: 'reports', label: 'Reports' },
  { key: 'group', label: 'Group' },
  { key: 'journal', label: 'Journal' },
  { key: 'coa', label: 'Chart of Accounts' },
  { key: 'ledger', label: 'Ledger' },
  { key: 'assets', label: 'Fixed Assets' },
  { key: 'bank', label: 'Bank' },
  { key: 'periods', label: 'Periods' },
  { key: 'setup', label: 'Setup' },
];

const TEMPLATES = [
  { key: 'retail', label: 'Retail / Shop' },
  { key: 'distribution', label: 'Distribution' },
  { key: 'logistics', label: 'Logistics' },
  { key: 'services', label: 'Services' },
  { key: 'head_office', label: 'Head Office' },
];

const RULE_LABELS = {
  'daily_financial.revenue': 'Daily report — Revenue',
  'daily_financial.expenses': 'Daily report — Expenses',
  expense_request: 'Approved expense request',
};

const SRC_BADGE = {
  manual: 'bg-slate-500/10 border-slate-500/20 text-slate-600 dark:text-slate-300',
  daily_financial: 'bg-sky-500/10 border-sky-500/20 text-sky-600 dark:text-sky-400',
  expense: 'bg-violet-500/10 border-violet-500/20 text-violet-600 dark:text-violet-400',
  opening: 'bg-amber-500/10 border-amber-500/20 text-amber-600 dark:text-amber-400',
  reversal: 'bg-red-500/10 border-red-500/20 text-red-600 dark:text-red-400',
};

function money(v) {
  return `Ks ${Number(v || 0).toLocaleString()}`;
}

const thisPeriod = () => new Date().toISOString().slice(0, 7);
const today = () => new Date().toISOString().slice(0, 10);

export default function AccountingPage() {
  const [tab, setTab] = useState('reports');
  const [bus, setBus] = useState([]);
  const [buId, setBuId] = useState('');
  const [period, setPeriod] = useState(thisPeriod());
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const [coa, setCoa] = useState([]);
  const [template, setTemplate] = useState('retail');
  const [newAcc, setNewAcc] = useState({ code: '', name: '', account_type: 'expense' });

  const [journal, setJournal] = useState([]);
  const [jForm, setJForm] = useState({ entry_date: today(), description: '', lines: [
    { account_id: '', debit: '', credit: '' }, { account_id: '', debit: '', credit: '' },
  ] });

  const [tb, setTb] = useState(null);
  const [pl, setPl] = useState(null);
  const [bs, setBs] = useState(null);
  const [ledgerAcc, setLedgerAcc] = useState('');
  const [ledger, setLedger] = useState(null);
  const [periods, setPeriods] = useState([]);
  const [gpl, setGpl] = useState(null);
  const [gbs, setGbs] = useState(null);
  const [gcomp, setGcomp] = useState([]);
  const [assets, setAssets] = useState([]);
  const [assetForm, setAssetForm] = useState({ name: '', category: '', purchase_date: today(), cost: '', salvage_value: 0, useful_life_months: 60 });
  const [bankAccounts, setBankAccounts] = useState([]);
  const [bankForm, setBankForm] = useState({ bank_name: '', account_number: '', account_id: '' });
  const [selectedBank, setSelectedBank] = useState('');
  const [bankTxns, setBankTxns] = useState([]);
  const [recon, setRecon] = useState(null);
  const [txnForm, setTxnForm] = useState({ txn_date: today(), description: '', deposit: '', withdrawal: '' });
  const [rulesData, setRulesData] = useState(null);
  const [ruleEdits, setRuleEdits] = useState({});
  const [obDate, setObDate] = useState(today());
  const [obFile, setObFile] = useState(null);

  useEffect(() => {
    api.adminOverview().then((d) => {
      const ops = d.operations || [];
      setBus(ops);
      if (ops.length && !buId) setBuId(String(ops[0].bu_id));
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadCoa = useCallback(async () => {
    if (!buId) return;
    try { setCoa((await api.accCoa(Number(buId))).items); } catch (e) { setError(e.message); }
  }, [buId]);

  useEffect(() => { loadCoa(); }, [loadCoa]);

  const load = useCallback(async () => {
    if (!buId) return;
    setError('');
    try {
      const bu = Number(buId);
      if (tab === 'reports') {
        setTb(await api.accTrialBalance(bu, period));
        setPl(await api.accPL(bu, period));
        setBs(await api.accBS(bu, period));
      }
      if (tab === 'group') {
        setGpl(await api.accConsolidatedPL(period));
        setGbs(await api.accConsolidatedBS(period));
        setGcomp((await api.accBuComparison(period)).items);
      }
      if (tab === 'journal') setJournal((await api.accJournal(bu, {})).items);
      if (tab === 'periods') setPeriods((await api.accPeriods(bu)).items);
      if (tab === 'assets') setAssets((await api.accFixedAssets(bu)).items);
      if (tab === 'setup') {
        const data = await api.accPostingRules();
        setRulesData(data);
        const buNum = Number(buId);
        const edits = {};
        for (const d of data.defaults) {
          const buRule = data.items.find((r) => r.business_unit_id === buNum && r.source_field === d.source_field);
          const groupRule = data.items.find((r) => r.business_unit_id === null && r.source_field === d.source_field);
          const eff = buRule || groupRule || d;
          edits[d.source_field] = { dr: eff.debit_account_code, cr: eff.credit_account_code, scope: buRule ? 'BU override' : groupRule ? 'Group rule' : 'Built-in default' };
        }
        setRuleEdits(edits);
      }
      if (tab === 'bank') {
        const list = (await api.accBankAccounts(bu)).items;
        setBankAccounts(list);
        if (selectedBank) {
          setBankTxns((await api.accBankTxns(Number(selectedBank))).items);
          setRecon(await api.accReconciliation(Number(selectedBank)));
        }
      }
      if (tab === 'ledger' && ledgerAcc) setLedger(await api.accLedger(Number(ledgerAcc), period));
    } catch (e) { setError(e.message); }
  }, [tab, buId, period, ledgerAcc, selectedBank]);

  useEffect(() => { load(); }, [load]);

  function flash(msg) { setNotice(msg); setTimeout(() => setNotice(''), 4500); }

  async function provision() {
    try {
      const d = await api.accProvision(Number(buId), template);
      flash(d.created ? `Chart of accounts created (${d.created} accounts, ${template}).` : 'This BU already has a chart of accounts.');
      loadCoa();
    } catch (e) { setError(e.message); }
  }

  async function addAccount(e) {
    e.preventDefault();
    if (!newAcc.code.trim() || !newAcc.name.trim()) return;
    try {
      await api.accCreateAccount({ business_unit_id: Number(buId), code: newAcc.code.trim(), name: newAcc.name.trim(), account_type: newAcc.account_type });
      setNewAcc({ code: '', name: '', account_type: 'expense' });
      loadCoa();
    } catch (err) { setError(err.message); }
  }

  const jTotals = jForm.lines.reduce((a, l) => ({
    dr: a.dr + (Number(l.debit) || 0), cr: a.cr + (Number(l.credit) || 0),
  }), { dr: 0, cr: 0 });

  async function createJournal(e) {
    e.preventDefault();
    const lines = jForm.lines.filter((l) => l.account_id).map((l) => ({
      account_id: Number(l.account_id), debit: Number(l.debit) || 0, credit: Number(l.credit) || 0,
    }));
    if (lines.length < 2) { setError('Add at least two lines'); return; }
    try {
      await api.accCreateJournal({ business_unit_id: Number(buId), entry_date: jForm.entry_date, description: jForm.description, lines });
      setJForm({ entry_date: today(), description: '', lines: [{ account_id: '', debit: '', credit: '' }, { account_id: '', debit: '', credit: '' }] });
      flash('Draft entry created. Post it when ready.');
      load();
    } catch (err) { setError(err.message); }
  }

  async function postEntry(id) { try { await api.accPostJournal(id); load(); } catch (e) { setError(e.message); } }
  async function reverseEntry(id) { try { await api.accReverseJournal(id); flash('Entry reversed.'); load(); } catch (e) { setError(e.message); } }
  async function deleteEntry(id) { try { await api.accDeleteJournal(id); load(); } catch (e) { setError(e.message); } }

  async function saveRule(field) {
    const edit = ruleEdits[field];
    if (!edit) return;
    try {
      await api.accSavePostingRule({ business_unit_id: Number(buId), source_field: field, debit_account_code: edit.dr, credit_account_code: edit.cr });
      flash(`Posting rule saved for ${buName} — ${field}.`);
      load();
    } catch (e) { setError(e.message); }
  }

  async function uploadOpening(e) {
    e.preventDefault();
    if (!obFile) { setError('Choose the filled opening-balance Excel file first'); return; }
    try {
      const entry = await api.accUploadOpeningBalance(Number(buId), obDate, obFile);
      flash(`Opening balances posted as ${entry.entry_no} for ${buName}.`);
      setObFile(null);
    } catch (err) { setError(err.message); }
  }

  async function addAsset(e) {
    e.preventDefault();
    if (!assetForm.name.trim() || !assetForm.cost) return;
    try {
      await api.accCreateAsset({ business_unit_id: Number(buId), name: assetForm.name.trim(), category: assetForm.category || undefined, purchase_date: assetForm.purchase_date, cost: Number(assetForm.cost), salvage_value: Number(assetForm.salvage_value) || 0, useful_life_months: Number(assetForm.useful_life_months) || 60 });
      setAssetForm({ name: '', category: '', purchase_date: today(), cost: '', salvage_value: 0, useful_life_months: 60 });
      load();
    } catch (err) { setError(err.message); }
  }

  async function runDepreciation() {
    try {
      const d = await api.accDepreciate({ business_unit_id: Number(buId), period });
      flash(`Depreciation ${period}: ${d.assets_depreciated} asset(s), ${money(d.total)}${d.journal_posted ? ' — journal posted' : ''}.`);
      load();
    } catch (e) { setError(e.message); }
  }

  async function addBankAccount(e) {
    e.preventDefault();
    if (!bankForm.bank_name.trim()) return;
    try {
      await api.accCreateBankAccount({ business_unit_id: Number(buId), bank_name: bankForm.bank_name.trim(), account_number: bankForm.account_number || undefined, account_id: bankForm.account_id ? Number(bankForm.account_id) : undefined });
      setBankForm({ bank_name: '', account_number: '', account_id: '' });
      load();
    } catch (err) { setError(err.message); }
  }

  async function addTxn(e) {
    e.preventDefault();
    if (!selectedBank) return;
    try {
      await api.accAddBankTxn(Number(selectedBank), { txn_date: txnForm.txn_date, description: txnForm.description || undefined, deposit: Number(txnForm.deposit) || 0, withdrawal: Number(txnForm.withdrawal) || 0 });
      setTxnForm({ txn_date: today(), description: '', deposit: '', withdrawal: '' });
      load();
    } catch (err) { setError(err.message); }
  }

  async function closePeriod() {
    try { await api.accClosePeriod({ business_unit_id: Number(buId), period }); flash(`Period ${period} closed.`); load(); }
    catch (e) { setError(e.message); }
  }
  async function reopenPeriod(p) {
    try { await api.accReopenPeriod({ business_unit_id: Number(buId), period: p }); flash(`Period ${p} reopened.`); load(); }
    catch (e) { setError(e.message); }
  }

  const postable = coa.filter((a) => !a.is_header && a.is_active);
  const buName = bus.find((b) => b.bu_id === Number(buId))?.bu_name || '';

  return (
    <div className={`min-h-screen ${PAGE_BG} text-slate-800 dark:text-slate-200 antialiased flex flex-col lg:flex-row`}>
      <AdminSidebar />
      <main className="flex-1 min-w-0 px-4 sm:px-6 py-6 sm:py-8 space-y-6 fade-in">
        <header className={`${GLASS} px-5 sm:px-6 py-4 flex items-center justify-between flex-wrap gap-4`}>
          <div>
            <h1 className="font-bold text-slate-900 dark:text-slate-100 text-lg">Accounting</h1>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Double-entry books per business unit — journal, ledger, trial balance, P&L, balance sheet</p>
          </div>
          <div className="flex items-center gap-3 flex-wrap">
            <select value={buId} onChange={(e) => setBuId(e.target.value)} className="text-sm font-semibold bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2">
              {bus.map((b) => <option key={b.bu_id} value={b.bu_id}>{b.bu_name}</option>)}
            </select>
            <input type="month" value={period} onChange={(e) => setPeriod(e.target.value)} className="text-sm font-semibold bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2" />
          </div>
        </header>

        {notice && <div className="text-sm font-medium text-emerald-700 dark:text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-4 py-2.5">{notice}</div>}
        {error && <div className="text-sm font-medium text-red-700 dark:text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-2.5">{error}</div>}

        <div className="flex items-center gap-1 bg-slate-100 dark:bg-slate-800 rounded-lg p-1 flex-wrap w-fit">
          {TABS.map((t) => (
            <button key={t.key} onClick={() => setTab(t.key)} className={`text-xs font-semibold px-3.5 py-1.5 rounded-md transition-colors whitespace-nowrap ${tab === t.key ? 'bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100 shadow-sm' : 'text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-300'}`}>
              {t.label}
            </button>
          ))}
        </div>

        {tab === 'reports' && (
          <div className="space-y-6">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
              <span className="font-bold uppercase tracking-wider text-slate-400">Export {period}:</span>
              {[['pl', 'P&L'], ['bs', 'Balance Sheet'], ['tb', 'Trial Balance'], ['journal', 'Journal']].map(([r, label]) => (
                <span key={r} className="flex items-center gap-1.5">
                  <span className="font-semibold text-slate-600 dark:text-slate-300">{label}</span>
                  <a href={api.accExportUrl(r, 'xlsx', Number(buId), period)} className="font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Excel</a>
                  <a href={api.accExportUrl(r, 'pdf', Number(buId), period)} className="font-semibold text-red-500 hover:underline">PDF</a>
                </span>
              ))}
            </div>
            {pl && (
              <section className="grid sm:grid-cols-3 gap-4">
                {[
                  { label: `Income — ${period}`, value: money(pl.total_income) },
                  { label: `Expenses — ${period}`, value: money(pl.total_expenses) },
                  { label: 'Net Profit', value: money(pl.net_profit), accent: pl.net_profit >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500' },
                ].map((c) => (
                  <div key={c.label} className={`${GLASS} p-5`}>
                    <p className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-[0.08em]">{c.label}</p>
                    <p className={`font-bold text-slate-900 dark:text-slate-100 text-[24px] leading-tight mt-1.5 tabular-nums ${c.accent || ''}`}>{c.value}</p>
                  </div>
                ))}
              </section>
            )}
            <div className="grid lg:grid-cols-2 gap-6 items-start">
              <section className={`${GLASS} p-6`}>
                <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">Profit & Loss — {period}</h2>
                {pl && <>
                  <p className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-2">Income</p>
                  {pl.income.map((i) => <div key={i.code} className="flex justify-between text-sm py-1"><span>{i.code} {i.name}</span><span className="tabular-nums whitespace-nowrap pl-3">{money(i.amount)}</span></div>)}
                  {pl.income.length === 0 && <p className="text-sm text-slate-400 py-1">No income posted</p>}
                  <p className="text-xs font-bold uppercase tracking-wider text-slate-400 mt-4 mb-2">Expenses</p>
                  {pl.expenses.map((i) => <div key={i.code} className="flex justify-between text-sm py-1"><span>{i.code} {i.name}</span><span className="tabular-nums whitespace-nowrap pl-3">{money(i.amount)}</span></div>)}
                  {pl.expenses.length === 0 && <p className="text-sm text-slate-400 py-1">No expenses posted</p>}
                  <div className="border-t border-slate-200 dark:border-slate-700 mt-4 pt-3 flex justify-between font-bold text-slate-900 dark:text-slate-100"><span>Net Profit</span><span className="tabular-nums">{money(pl.net_profit)}</span></div>
                </>}
              </section>
              <section className={`${GLASS} p-6`}>
                <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">Balance Sheet {bs && (bs.balanced ? <span className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 ml-2">balanced ✓</span> : <span className="text-xs font-semibold text-red-500 ml-2">NOT balanced</span>)}</h2>
                {bs && <>
                  <p className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-2">Assets — {money(bs.total_assets)}</p>
                  {bs.assets.map((i) => <div key={i.code + i.name} className="flex justify-between text-sm py-1"><span>{i.code} {i.name}</span><span className="tabular-nums whitespace-nowrap pl-3">{money(i.amount)}</span></div>)}
                  <p className="text-xs font-bold uppercase tracking-wider text-slate-400 mt-4 mb-2">Liabilities — {money(bs.total_liabilities)}</p>
                  {bs.liabilities.map((i) => <div key={i.code + i.name} className="flex justify-between text-sm py-1"><span>{i.code} {i.name}</span><span className="tabular-nums whitespace-nowrap pl-3">{money(i.amount)}</span></div>)}
                  <p className="text-xs font-bold uppercase tracking-wider text-slate-400 mt-4 mb-2">Equity — {money(bs.total_equity)}</p>
                  {bs.equity.map((i) => <div key={i.code + i.name} className="flex justify-between text-sm py-1"><span>{i.code} {i.name}</span><span className="tabular-nums whitespace-nowrap pl-3">{money(i.amount)}</span></div>)}
                </>}
              </section>
            </div>
            <section className={`${GLASS} p-6 overflow-x-auto`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">Trial Balance {tb && <span className={`text-xs font-semibold ml-2 ${tb.balanced ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500'}`}>{tb.balanced ? 'balanced ✓' : 'NOT balanced'} · Dr {money(tb.total_debit)} / Cr {money(tb.total_credit)}</span>}</h2>
              <table className="w-full text-sm min-w-[560px]">
                <thead className="text-left text-xs uppercase tracking-wider text-slate-400 dark:text-slate-500">
                  <tr><th className="py-2 pr-3">Code</th><th className="py-2 pr-3">Account</th><th className="py-2 pr-3 text-right">Debit</th><th className="py-2 pr-3 text-right">Credit</th><th className="py-2 text-right">Balance</th></tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {tb?.items.map((i) => (
                    <tr key={i.account_id}>
                      <td className="py-2 pr-3 tabular-nums text-slate-500">{i.code}</td>
                      <td className="py-2 pr-3 font-medium">{i.name}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{i.debit ? money(i.debit) : '—'}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{i.credit ? money(i.credit) : '—'}</td>
                      <td className="py-2 text-right tabular-nums font-semibold">{money(Math.abs(i.balance))} {i.balance_side === 'credit' ? 'Cr' : 'Dr'}</td>
                    </tr>
                  ))}
                  {tb?.items.length === 0 && <tr><td colSpan={5} className="py-8 text-center text-slate-400">No posted entries yet — provision the chart of accounts first (Chart of Accounts tab)</td></tr>}
                </tbody>
              </table>
            </section>
          </div>
        )}

        {tab === 'group' && (
          <div className="space-y-6">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
              <span className="font-bold uppercase tracking-wider text-slate-400">Export group {period}:</span>
              {[['consolidated-pl', 'Consolidated P&L'], ['consolidated-bs', 'Consolidated Balance Sheet']].map(([r, label]) => (
                <span key={r} className="flex items-center gap-1.5">
                  <span className="font-semibold text-slate-600 dark:text-slate-300">{label}</span>
                  <a href={api.accExportUrl(r, 'xlsx', null, period)} className="font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Excel</a>
                  <a href={api.accExportUrl(r, 'pdf', null, period)} className="font-semibold text-red-500 hover:underline">PDF</a>
                </span>
              ))}
            </div>
            {gpl && (
              <section className="grid sm:grid-cols-3 gap-4">
                {[
                  { label: `Group Income — ${period}`, value: money(gpl.total_income) },
                  { label: `Group Expenses — ${period}`, value: money(gpl.total_expenses) },
                  { label: 'Group Net Profit', value: money(gpl.net_profit), accent: gpl.net_profit >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500' },
                ].map((c) => (
                  <div key={c.label} className={`${GLASS} p-5`}>
                    <p className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-[0.08em]">{c.label}</p>
                    <p className={`font-bold text-slate-900 dark:text-slate-100 text-[24px] leading-tight mt-1.5 tabular-nums ${c.accent || ''}`}>{c.value}</p>
                  </div>
                ))}
              </section>
            )}
            <section className={`${GLASS} p-6 overflow-x-auto`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">BU Comparison — {period}</h2>
              <table className="w-full text-sm min-w-[540px]">
                <thead className="text-left text-xs uppercase tracking-wider text-slate-400 dark:text-slate-500">
                  <tr><th className="py-2 pr-3">Business Unit</th><th className="py-2 pr-3 text-right">Income</th><th className="py-2 pr-3 text-right">Expenses</th><th className="py-2 text-right">Net Profit</th></tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {gcomp.map((b) => (
                    <tr key={b.business_unit_id}>
                      <td className="py-2 pr-3 font-medium">{b.bu_name}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{money(b.income)}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{money(b.expenses)}</td>
                      <td className={`py-2 text-right tabular-nums font-semibold ${b.net_profit >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500'}`}>{money(b.net_profit)}</td>
                    </tr>
                  ))}
                  {gcomp.length === 0 && <tr><td colSpan={4} className="py-8 text-center text-slate-400">No posted accounting data for this period yet</td></tr>}
                </tbody>
              </table>
            </section>
            <div className="grid lg:grid-cols-2 gap-6 items-start">
              <section className={`${GLASS} p-6`}>
                <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">Consolidated P&L — {period}</h2>
                {gpl && <>
                  <p className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-2">Income</p>
                  {gpl.income.map((i) => <div key={i.code} className="flex justify-between text-sm py-1"><span>{i.code} {i.name}</span><span className="tabular-nums whitespace-nowrap pl-3">{money(i.amount)}</span></div>)}
                  {gpl.income.length === 0 && <p className="text-sm text-slate-400 py-1">No income posted</p>}
                  <p className="text-xs font-bold uppercase tracking-wider text-slate-400 mt-4 mb-2">Expenses</p>
                  {gpl.expenses.map((i) => <div key={i.code} className="flex justify-between text-sm py-1"><span>{i.code} {i.name}</span><span className="tabular-nums whitespace-nowrap pl-3">{money(i.amount)}</span></div>)}
                  {gpl.expenses.length === 0 && <p className="text-sm text-slate-400 py-1">No expenses posted</p>}
                  <div className="border-t border-slate-200 dark:border-slate-700 mt-4 pt-3 flex justify-between font-bold text-slate-900 dark:text-slate-100"><span>Group Net Profit</span><span className="tabular-nums">{money(gpl.net_profit)}</span></div>
                </>}
              </section>
              <section className={`${GLASS} p-6`}>
                <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">Consolidated Balance Sheet {gbs && (gbs.balanced ? <span className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 ml-2">balanced ✓</span> : <span className="text-xs font-semibold text-red-500 ml-2">NOT balanced</span>)}</h2>
                {gbs && <>
                  {gbs.intercompany_eliminated > 0 && <p className="text-xs text-slate-500 dark:text-slate-400 mb-3">Intercompany balances eliminated: {money(gbs.intercompany_eliminated)}</p>}
                  <p className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-2">Assets — {money(gbs.total_assets)}</p>
                  {gbs.assets.map((i) => <div key={i.code + i.name} className="flex justify-between text-sm py-1"><span>{i.code} {i.name}</span><span className="tabular-nums whitespace-nowrap pl-3">{money(i.amount)}</span></div>)}
                  <p className="text-xs font-bold uppercase tracking-wider text-slate-400 mt-4 mb-2">Liabilities — {money(gbs.total_liabilities)}</p>
                  {gbs.liabilities.map((i) => <div key={i.code + i.name} className="flex justify-between text-sm py-1"><span>{i.code} {i.name}</span><span className="tabular-nums whitespace-nowrap pl-3">{money(i.amount)}</span></div>)}
                  <p className="text-xs font-bold uppercase tracking-wider text-slate-400 mt-4 mb-2">Equity — {money(gbs.total_equity)}</p>
                  {gbs.equity.map((i) => <div key={i.code + i.name} className="flex justify-between text-sm py-1"><span>{i.code} {i.name}</span><span className="tabular-nums whitespace-nowrap pl-3">{money(i.amount)}</span></div>)}
                </>}
              </section>
            </div>
          </div>
        )}

        {tab === 'journal' && (
          <div className="space-y-6">
            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">New Journal Entry — {buName}</h2>
              <form onSubmit={createJournal} className="space-y-3">
                <div className="flex flex-wrap gap-3">
                  <input type="date" value={jForm.entry_date} onChange={(e) => setJForm({ ...jForm, entry_date: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                  <input value={jForm.description} onChange={(e) => setJForm({ ...jForm, description: e.target.value })} placeholder="Description" className="flex-1 min-w-[220px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm" />
                </div>
                {jForm.lines.map((l, idx) => (
                  <div key={idx} className="flex flex-wrap gap-2 items-center">
                    <select value={l.account_id} onChange={(e) => { const lines = [...jForm.lines]; lines[idx].account_id = e.target.value; setJForm({ ...jForm, lines }); }} className="flex-1 min-w-[220px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-sm">
                      <option value="">Select account…</option>
                      {postable.map((a) => <option key={a.id} value={a.id}>{a.code} — {a.name}</option>)}
                    </select>
                    <input type="number" placeholder="Debit" value={l.debit} onChange={(e) => { const lines = [...jForm.lines]; lines[idx].debit = e.target.value; setJForm({ ...jForm, lines }); }} className="w-32 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-sm" />
                    <input type="number" placeholder="Credit" value={l.credit} onChange={(e) => { const lines = [...jForm.lines]; lines[idx].credit = e.target.value; setJForm({ ...jForm, lines }); }} className="w-32 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-sm" />
                    {jForm.lines.length > 2 && <button type="button" onClick={() => setJForm({ ...jForm, lines: jForm.lines.filter((_, i) => i !== idx) })} className="text-xs font-semibold text-red-500">Remove</button>}
                  </div>
                ))}
                <div className="flex items-center justify-between flex-wrap gap-3 pt-1">
                  <button type="button" onClick={() => setJForm({ ...jForm, lines: [...jForm.lines, { account_id: '', debit: '', credit: '' }] })} className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:underline">+ Add line</button>
                  <p className={`text-sm font-semibold tabular-nums ${jTotals.dr === jTotals.cr && jTotals.dr > 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-500'}`}>Dr {money(jTotals.dr)} · Cr {money(jTotals.cr)}{jTotals.dr === jTotals.cr && jTotals.dr > 0 ? ' ✓ balanced' : ''}</p>
                  <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Save draft</button>
                </div>
              </form>
            </section>
            <section className={`${GLASS} p-6`}>
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {journal.map((e) => (
                  <li key={e.id} className="py-3 flex items-center justify-between gap-3 flex-wrap">
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">{e.entry_no} <span className="font-normal text-slate-400">· {e.entry_date} · {money(e.total_debit)}</span></p>
                      <p className="text-xs text-slate-400 dark:text-slate-500 truncate">{e.description || '—'}{e.reversed_by_id ? ' · reversed' : ''}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${SRC_BADGE[e.source] || SRC_BADGE.manual}`}>{e.source.replace('_', ' ')}</span>
                      <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${e.status === 'posted' ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600 dark:text-emerald-400' : 'bg-amber-500/10 border-amber-500/20 text-amber-600 dark:text-amber-400'}`}>{e.status}</span>
                      {e.status === 'draft' && <button onClick={() => postEntry(e.id)} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Post</button>}
                      {e.status === 'draft' && <button onClick={() => deleteEntry(e.id)} className="text-xs font-semibold text-red-500 hover:underline">Delete</button>}
                      {e.status === 'posted' && !e.reversed_by_id && e.source !== 'reversal' && <button onClick={() => reverseEntry(e.id)} className="text-xs font-semibold text-red-500 hover:underline">Reverse</button>}
                    </div>
                  </li>
                ))}
                {journal.length === 0 && <li className="py-8 text-center text-sm text-slate-400">No journal entries for this BU yet</li>}
              </ul>
            </section>
          </div>
        )}

        {tab === 'coa' && (
          <section className={`${GLASS} p-6`}>
            {coa.length === 0 ? (
              <div className="text-center py-6">
                <p className="text-sm text-slate-500 dark:text-slate-400 mb-4">{buName} has no chart of accounts yet. Create one from a standard template:</p>
                <div className="flex items-center justify-center gap-3 flex-wrap">
                  <select value={template} onChange={(e) => setTemplate(e.target.value)} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm font-semibold">
                    {TEMPLATES.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
                  </select>
                  <button onClick={provision} className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Create chart of accounts</button>
                </div>
              </div>
            ) : (
              <>
                <form onSubmit={addAccount} className="flex flex-wrap gap-3 mb-5">
                  <input value={newAcc.code} onChange={(e) => setNewAcc({ ...newAcc, code: e.target.value })} placeholder="Code (e.g. 6150)" className="w-36 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                  <input value={newAcc.name} onChange={(e) => setNewAcc({ ...newAcc, name: e.target.value })} placeholder="Account name" className="flex-1 min-w-[200px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm" />
                  <select value={newAcc.account_type} onChange={(e) => setNewAcc({ ...newAcc, account_type: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                    {['asset', 'liability', 'equity', 'income', 'expense'].map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                  <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Add account</button>
                </form>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm min-w-[520px]">
                    <thead className="text-left text-xs uppercase tracking-wider text-slate-400 dark:text-slate-500">
                      <tr><th className="py-2 pr-3">Code</th><th className="py-2 pr-3">Name</th><th className="py-2 pr-3">Type</th><th className="py-2 text-right">Status</th></tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                      {coa.map((a) => (
                        <tr key={a.id} className={a.is_header ? 'bg-slate-50 dark:bg-slate-800/50 font-semibold' : ''}>
                          <td className="py-2 pr-3 tabular-nums text-slate-500">{a.code}</td>
                          <td className="py-2 pr-3">{a.name}</td>
                          <td className="py-2 pr-3 capitalize text-slate-500">{a.account_type}</td>
                          <td className="py-2 text-right">
                            {a.is_header ? <span className="text-[10px] font-bold text-slate-400">HEADER</span> : a.is_active
                              ? <button onClick={async () => { await api.accUpdateAccount(a.id, { is_active: false }); loadCoa(); }} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Active</button>
                              : <button onClick={async () => { await api.accUpdateAccount(a.id, { is_active: true }); loadCoa(); }} className="text-xs font-semibold text-slate-400 hover:underline">Inactive</button>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </section>
        )}

        {tab === 'ledger' && (
          <section className={`${GLASS} p-6`}>
            <div className="flex items-center gap-3 mb-5 flex-wrap">
              <select value={ledgerAcc} onChange={(e) => setLedgerAcc(e.target.value)} className="flex-1 min-w-[240px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm font-semibold">
                <option value="">Select account…</option>
                {postable.map((a) => <option key={a.id} value={a.id}>{a.code} — {a.name}</option>)}
              </select>
              {ledger && <p className="text-sm font-semibold">Balance: <span className="tabular-nums">{money(ledger.balance)}</span></p>}
            </div>
            <table className="w-full text-sm min-w-[560px]">
              <thead className="text-left text-xs uppercase tracking-wider text-slate-400 dark:text-slate-500">
                <tr><th className="py-2 pr-3">Date</th><th className="py-2 pr-3">Entry</th><th className="py-2 pr-3">Description</th><th className="py-2 pr-3 text-right">Debit</th><th className="py-2 pr-3 text-right">Credit</th><th className="py-2 text-right">Balance</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {ledger?.items.map((i) => (
                  <tr key={i.entry_id + i.date}>
                    <td className="py-2 pr-3 tabular-nums">{i.date}</td>
                    <td className="py-2 pr-3 text-slate-500">{i.entry_no}</td>
                    <td className="py-2 pr-3">{i.description}</td>
                    <td className="py-2 pr-3 text-right tabular-nums">{i.debit ? money(i.debit) : '—'}</td>
                    <td className="py-2 pr-3 text-right tabular-nums">{i.credit ? money(i.credit) : '—'}</td>
                    <td className="py-2 text-right tabular-nums font-semibold">{money(i.balance)}</td>
                  </tr>
                ))}
                {(!ledger || ledger.items.length === 0) && <tr><td colSpan={6} className="py-8 text-center text-slate-400">{ledgerAcc ? 'No movements for this account' : 'Pick an account to view its ledger'}</td></tr>}
              </tbody>
            </table>
          </section>
        )}

        {tab === 'assets' && (
          <section className={`${GLASS} p-6`}>
            <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
              <h2 className="font-bold text-slate-900 dark:text-slate-100">Fixed Assets — {buName}</h2>
              <button onClick={runDepreciation} className="whitespace-nowrap text-xs font-semibold bg-violet-500/10 border border-violet-500/20 text-violet-600 dark:text-violet-400 px-3.5 py-2 rounded-lg hover:bg-violet-500/20 transition-colors">Run depreciation — {period}</button>
            </div>
            <form onSubmit={addAsset} className="flex flex-wrap gap-3 mb-5">
              <input value={assetForm.name} onChange={(e) => setAssetForm({ ...assetForm, name: e.target.value })} placeholder="Asset name (e.g. Delivery Truck)" className="flex-1 min-w-[200px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm" />
              <input value={assetForm.category} onChange={(e) => setAssetForm({ ...assetForm, category: e.target.value })} placeholder="Category" className="w-36 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
              <input type="date" value={assetForm.purchase_date} onChange={(e) => setAssetForm({ ...assetForm, purchase_date: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
              <input type="number" value={assetForm.cost} onChange={(e) => setAssetForm({ ...assetForm, cost: e.target.value })} placeholder="Cost" className="w-36 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
              <input type="number" value={assetForm.useful_life_months} onChange={(e) => setAssetForm({ ...assetForm, useful_life_months: e.target.value })} title="Useful life (months)" className="w-28 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
              <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Add asset</button>
            </form>
            <div className="overflow-x-auto">
              <table className="w-full text-sm min-w-[680px]">
                <thead className="text-left text-xs uppercase tracking-wider text-slate-400 dark:text-slate-500">
                  <tr><th className="py-2 pr-3">Asset</th><th className="py-2 pr-3 text-right">Cost</th><th className="py-2 pr-3 text-right">Accum. Depr.</th><th className="py-2 pr-3 text-right">Book Value</th><th className="py-2 pr-3 text-right">Monthly</th><th className="py-2 pr-3">Last Run</th><th className="py-2" /></tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {assets.map((a) => (
                    <tr key={a.id} className={a.status !== 'active' ? 'opacity-50' : ''}>
                      <td className="py-2 pr-3 font-medium">{a.name} <span className="text-xs text-slate-400 font-normal">{a.category || ''} · {a.purchase_date}</span></td>
                      <td className="py-2 pr-3 text-right tabular-nums">{money(a.cost)}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{money(a.accumulated_depreciation)}</td>
                      <td className="py-2 pr-3 text-right tabular-nums font-semibold">{money(a.book_value)}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{money(a.monthly_depreciation)}</td>
                      <td className="py-2 pr-3 text-slate-500">{a.last_depreciated_period || '—'}</td>
                      <td className="py-2 text-right">{a.status === 'active' && <button onClick={async () => { await api.accDisposeAsset(a.id); load(); }} className="text-xs font-semibold text-red-500 hover:underline">Dispose</button>}</td>
                    </tr>
                  ))}
                  {assets.length === 0 && <tr><td colSpan={7} className="py-8 text-center text-slate-400">No fixed assets yet</td></tr>}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {tab === 'bank' && (
          <div className="space-y-6">
            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">Bank Accounts — {buName}</h2>
              <form onSubmit={addBankAccount} className="flex flex-wrap gap-3 mb-5">
                <input value={bankForm.bank_name} onChange={(e) => setBankForm({ ...bankForm, bank_name: e.target.value })} placeholder="Bank name (e.g. KBZ)" className="flex-1 min-w-[180px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm" />
                <input value={bankForm.account_number} onChange={(e) => setBankForm({ ...bankForm, account_number: e.target.value })} placeholder="Account number" className="w-48 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <select value={bankForm.account_id} onChange={(e) => setBankForm({ ...bankForm, account_id: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                  <option value="">Ledger account…</option>
                  {postable.filter((a) => a.account_type === 'asset').map((a) => <option key={a.id} value={a.id}>{a.code} — {a.name}</option>)}
                </select>
                <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Add account</button>
              </form>
              <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {bankAccounts.map((b) => (
                  <button key={b.id} onClick={() => setSelectedBank(String(b.id))} className={`text-left p-4 rounded-xl border transition-colors ${selectedBank === String(b.id) ? 'border-brand-500 bg-brand-500/5' : 'border-slate-200 dark:border-slate-700 hover:border-slate-300 dark:hover:border-slate-600'}`}>
                    <p className="text-sm font-bold text-slate-900 dark:text-slate-100">{b.bank_name}</p>
                    <p className="text-xs text-slate-400">{b.account_number || '—'}{b.account_code ? ` · ledger ${b.account_code}` : ''}</p>
                    <p className="text-sm font-semibold tabular-nums mt-2">{money(b.statement_balance)}</p>
                    <p className="text-xs text-slate-400">{b.unreconciled_count} unreconciled</p>
                  </button>
                ))}
                {bankAccounts.length === 0 && <p className="text-sm text-slate-400">No bank accounts yet</p>}
              </div>
            </section>
            {selectedBank && (
              <section className={`${GLASS} p-6`}>
                {recon && (
                  <div className="flex flex-wrap gap-x-6 gap-y-1 mb-4 text-sm">
                    <p>Statement: <span className="font-bold tabular-nums">{money(recon.statement_balance)}</span></p>
                    <p>Ledger: <span className="font-bold tabular-nums">{recon.ledger_balance !== null ? money(recon.ledger_balance) : '—'}</span></p>
                    <p>Difference: <span className={`font-bold tabular-nums ${recon.difference === 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-600 dark:text-amber-400'}`}>{recon.difference !== null ? money(recon.difference) : '—'}</span></p>
                  </div>
                )}
                <form onSubmit={addTxn} className="flex flex-wrap gap-2 mb-4">
                  <input type="date" value={txnForm.txn_date} onChange={(e) => setTxnForm({ ...txnForm, txn_date: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-sm" />
                  <input value={txnForm.description} onChange={(e) => setTxnForm({ ...txnForm, description: e.target.value })} placeholder="Description" className="flex-1 min-w-[160px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-sm" />
                  <input type="number" value={txnForm.deposit} onChange={(e) => setTxnForm({ ...txnForm, deposit: e.target.value })} placeholder="Deposit" className="w-32 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-sm" />
                  <input type="number" value={txnForm.withdrawal} onChange={(e) => setTxnForm({ ...txnForm, withdrawal: e.target.value })} placeholder="Withdrawal" className="w-32 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-sm" />
                  <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2 rounded-lg transition-colors">Add line</button>
                </form>
                <table className="w-full text-sm min-w-[540px]">
                  <thead className="text-left text-xs uppercase tracking-wider text-slate-400 dark:text-slate-500">
                    <tr><th className="py-2 pr-3">Date</th><th className="py-2 pr-3">Description</th><th className="py-2 pr-3 text-right">Deposit</th><th className="py-2 pr-3 text-right">Withdrawal</th><th className="py-2 text-center">Reconciled</th></tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {bankTxns.map((t) => (
                      <tr key={t.id}>
                        <td className="py-2 pr-3 tabular-nums">{t.txn_date}</td>
                        <td className="py-2 pr-3">{t.description || '—'}</td>
                        <td className="py-2 pr-3 text-right tabular-nums">{t.deposit ? money(t.deposit) : '—'}</td>
                        <td className="py-2 pr-3 text-right tabular-nums">{t.withdrawal ? money(t.withdrawal) : '—'}</td>
                        <td className="py-2 text-center"><input type="checkbox" checked={t.is_reconciled} onChange={async (e) => { await api.accSetReconciled(t.id, e.target.checked); load(); }} className="accent-emerald-600 w-4 h-4" /></td>
                      </tr>
                    ))}
                    {bankTxns.length === 0 && <tr><td colSpan={5} className="py-6 text-center text-slate-400">No statement lines yet</td></tr>}
                  </tbody>
                </table>
              </section>
            )}
          </div>
        )}

        {tab === 'setup' && (
          <div className="space-y-6">
            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-1.5">Opening Balances — {buName}</h2>
              <p className="text-sm text-slate-500 dark:text-slate-400 mb-4">Starting a BU on the system: download the template (it lists this BU's accounts), fill in each account's opening debit/credit from the accountants' books, then upload it back. The sheet must balance — one posted opening entry is created.</p>
              <div className="flex flex-wrap items-end gap-3">
                <a href={api.accOpeningTemplateUrl(Number(buId))} className="whitespace-nowrap text-sm font-semibold bg-slate-600 hover:bg-slate-700 text-white px-4 py-2.5 rounded-lg transition-colors">Download template</a>
                <label className="block">
                  <span className="block text-xs font-semibold text-slate-400 mb-1">As of date</span>
                  <input type="date" value={obDate} onChange={(e) => setObDate(e.target.value)} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                </label>
                <form onSubmit={uploadOpening} className="flex items-end gap-3 flex-wrap">
                  <label className="block">
                    <span className="block text-xs font-semibold text-slate-400 mb-1">Filled Excel file</span>
                    <input type="file" accept=".xlsx" onChange={(e) => setObFile(e.target.files?.[0] || null)} className="block text-sm text-slate-500 file:mr-3 file:rounded-lg file:border-0 file:bg-slate-100 dark:file:bg-slate-800 file:px-3 file:py-2.5 file:text-sm file:font-semibold" />
                  </label>
                  <button type="submit" className="whitespace-nowrap bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Upload & post opening</button>
                </form>
              </div>
            </section>

            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-1.5">Auto-Posting Rules — {buName}</h2>
              <p className="text-sm text-slate-500 dark:text-slate-400 mb-4">Which accounts automatic journal entries use. Saving here creates a BU override for {buName}; other BUs keep the group rule.</p>
              <div className="overflow-x-auto">
                <table className="w-full text-sm min-w-[680px]">
                  <thead className="text-left text-xs uppercase tracking-wider text-slate-400 dark:text-slate-500">
                    <tr><th className="py-2 pr-3">Source</th><th className="py-2 pr-3">Debit account</th><th className="py-2 pr-3">Credit account</th><th className="py-2 pr-3">Current scope</th><th className="py-2" /></tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {Object.entries(ruleEdits).map(([field, edit]) => (
                      <tr key={field}>
                        <td className="py-2.5 pr-3 font-medium">{RULE_LABELS[field] || field}<span className="block text-xs text-slate-400 font-normal">{field}</span></td>
                        <td className="py-2.5 pr-3">
                          <select value={edit.dr} onChange={(e) => setRuleEdits((r) => ({ ...r, [field]: { ...edit, dr: e.target.value } }))} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-2.5 py-2 text-sm">
                            {postable.map((a) => <option key={a.id} value={a.code}>{a.code} — {a.name}</option>)}
                          </select>
                        </td>
                        <td className="py-2.5 pr-3">
                          <select value={edit.cr} onChange={(e) => setRuleEdits((r) => ({ ...r, [field]: { ...edit, cr: e.target.value } }))} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-2.5 py-2 text-sm">
                            {postable.map((a) => <option key={a.id} value={a.code}>{a.code} — {a.name}</option>)}
                          </select>
                        </td>
                        <td className="py-2.5 pr-3 text-slate-500">{edit.scope}</td>
                        <td className="py-2.5 text-right"><button onClick={() => saveRule(field)} className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:underline">Save for this BU</button></td>
                      </tr>
                    ))}
                    {Object.keys(ruleEdits).length === 0 && <tr><td colSpan={5} className="py-6 text-center text-slate-400">Loading rules…</td></tr>}
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        )}

        {tab === 'periods' && (
          <section className={`${GLASS} p-6`}>
            <div className="flex items-center justify-between flex-wrap gap-3 mb-5">
              <p className="text-sm text-slate-500 dark:text-slate-400">Posting into a closed period is blocked. Reopening needs Chairman/HO.</p>
              <button onClick={closePeriod} className="whitespace-nowrap text-xs font-semibold bg-red-500/10 border border-red-500/20 text-red-600 dark:text-red-400 px-3.5 py-2 rounded-lg hover:bg-red-500/20 transition-colors">Close {period}</button>
            </div>
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {periods.map((p) => (
                <li key={p.period} className="py-3 flex items-center justify-between">
                  <span className="text-sm font-semibold">{p.period}</span>
                  <div className="flex items-center gap-2">
                    <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${p.status === 'closed' ? 'bg-red-500/10 border-red-500/20 text-red-600 dark:text-red-400' : 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600 dark:text-emerald-400'}`}>{p.status}</span>
                    {p.status === 'closed' && <button onClick={() => reopenPeriod(p.period)} className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:underline">Reopen</button>}
                  </div>
                </li>
              ))}
              {periods.length === 0 && <li className="py-6 text-center text-sm text-slate-400">All periods are open (nothing closed yet)</li>}
            </ul>
          </section>
        )}
        <Footer />
      </main>
    </div>
  );
}
