import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { GLASS } from '../utils/glassTheme';

const fmtKs = (n) => `Ks ${Number(n || 0).toLocaleString()}`;

/**
 * Group finance snapshot from the accounting books (ERP Phase 1+):
 * this month's group P&L, cash position, AR/AP outstanding, tax payable.
 * Renders nothing until at least one BU has a chart of accounts.
 */
export default function FinanceStrip() {
  const [fin, setFin] = useState(null);

  useEffect(() => {
    api.accFinanceSummary().then(setFin).catch(() => setFin(null));
  }, []);

  if (!fin || !fin.has_books) return null;

  const cards = [
    { label: `Group income — ${fin.period}`, value: fmtKs(fin.income), tone: 'text-emerald-600 dark:text-emerald-400' },
    { label: 'Group net profit', value: fmtKs(fin.net_profit), tone: fin.net_profit >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500' },
    { label: 'Cash position (1110+1120)', value: fmtKs(fin.cash_position), tone: 'text-slate-900 dark:text-slate-100' },
    { label: 'Receivables outstanding', value: fmtKs(fin.ar_outstanding), tone: 'text-slate-900 dark:text-slate-100' },
    { label: 'Payables outstanding', value: fmtKs(fin.ap_outstanding), tone: 'text-slate-900 dark:text-slate-100' },
    { label: 'Commercial tax payable', value: fmtKs(fin.tax_payable), tone: 'text-amber-600 dark:text-amber-400' },
  ];

  return (
    <section className={`${GLASS} px-5 sm:px-6 py-4`}>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-[11px] font-bold uppercase tracking-[0.1em] text-slate-400 dark:text-slate-500">Group Finance — from the books</h2>
        <Link to="/accounting" className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:underline">Open Accounting →</Link>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        {cards.map((c) => (
          <div key={c.label} className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 px-4 py-3.5 shadow-sm">
            <p className="text-[10.5px] font-semibold uppercase tracking-wide whitespace-nowrap overflow-hidden text-ellipsis text-slate-400 dark:text-slate-500">{c.label}</p>
            <p className={`mt-1.5 text-[17px] font-extrabold tabular-nums tracking-tight ${c.tone}`}>{c.value}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
