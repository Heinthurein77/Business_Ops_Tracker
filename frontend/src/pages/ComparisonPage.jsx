import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Chart from 'react-apexcharts';
import { api } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import Footer from '../components/Footer';
import { useTheme } from '../context/ThemeContext';
import { BUDGET_BADGE_CLASS, BUDGET_STATUS_LABEL } from '../utils/quadrantMetrics';
import { GLASS, PAGE_BG } from '../utils/glassTheme';

const METRICS = {
  revenue: { label: 'Revenue', color: '#6366f1' },
  cash_flow: { label: 'Cash Flow', color: '#8b5cf6' },
  receivable: { label: 'Receivable', color: '#0ea5e9' },
  payable: { label: 'Payable', color: '#f97316' },
};

function formatMoney(v) {
  return `Ks ${Math.round(v).toLocaleString()}`;
}

export default function ComparisonPage() {
  const navigate = useNavigate();
  const { theme } = useTheme();
  const isDark = theme === 'dark';
  const [data, setData] = useState(null);
  const [metric, setMetric] = useState('cash_flow');
  const [trendBuId, setTrendBuId] = useState(null);

  useEffect(() => {
    api.adminComparison().then((d) => {
      setData(d);
      setTrendBuId((prev) => prev ?? d.trend_cash_flow_by_bu[0]?.bu_id ?? null);
    });
  }, []);

  const sorted = useMemo(() => {
    if (!data) return [];
    return [...data.comparison_data].sort((a, b) => b[metric] - a[metric]);
  }, [data, metric]);

  const average = useMemo(() => {
    if (sorted.length === 0) return 0;
    return sorted.reduce((sum, d) => sum + d[metric], 0) / sorted.length;
  }, [sorted, metric]);

  if (!data) {
    return (
      <div className={`min-h-screen flex items-center justify-center ${PAGE_BG}`}>
        <div className="w-8 h-8 border-2 border-slate-200 dark:border-slate-700 border-t-brand-600 rounded-full animate-spin" />
      </div>
    );
  }

  const trendBu = data.trend_cash_flow_by_bu.find((b) => b.bu_id === trendBuId) || data.trend_cash_flow_by_bu[0] || null;

  const trendChart = {
    series: [{ name: 'Cash Flow', data: trendBu?.cash_flow || [] }],
    options: {
      chart: { type: 'area', height: 300, toolbar: { show: false }, fontFamily: 'Inter, sans-serif' },
      colors: ['#8b5cf6'],
      stroke: { curve: 'smooth', width: 2.5 },
      fill: { type: 'gradient', gradient: { shadeIntensity: 1, opacityFrom: 0.35, opacityTo: 0.02, stops: [0, 90, 100] } },
      dataLabels: { enabled: false },
      xaxis: { categories: data.trend_labels, labels: { style: { colors: '#94a3b8', fontSize: '11px' } }, axisBorder: { show: false }, axisTicks: { show: false } },
      yaxis: { labels: { formatter: (v) => 'Ks ' + (v / 1000).toFixed(0) + 'k', style: { colors: '#94a3b8', fontSize: '11px' } } },
      grid: { borderColor: isDark ? '#334155' : '#eef2f7' },
      legend: { show: false },
      tooltip: { theme: isDark ? 'dark' : 'light', y: { formatter: (v) => formatMoney(v) } },
    },
  };

  const barChart = {
    series: [
      { name: 'Receivable', data: data.bar_receivable },
      { name: 'Payable', data: data.bar_payable },
    ],
    options: {
      chart: { type: 'bar', height: 300, toolbar: { show: false }, fontFamily: 'Inter, sans-serif' },
      colors: ['#0ea5e9', '#f97316'],
      plotOptions: { bar: { borderRadius: 3, columnWidth: '65%' } },
      dataLabels: { enabled: false },
      xaxis: { categories: data.bar_categories, labels: { style: { colors: '#94a3b8', fontSize: '9px' }, rotate: -45 }, axisBorder: { show: false }, axisTicks: { show: false } },
      yaxis: { labels: { formatter: (v) => 'Ks ' + (v / 1000).toFixed(0) + 'k', style: { colors: '#94a3b8', fontSize: '11px' } } },
      grid: { borderColor: isDark ? '#334155' : '#eef2f7' },
      legend: { position: 'top', horizontalAlign: 'right', fontSize: '12px', labels: { colors: isDark ? '#94a3b8' : '#64748b' } },
      tooltip: { theme: isDark ? 'dark' : 'light', y: { formatter: (v) => formatMoney(v) } },
    },
  };

  const rankingChart = {
    series: [{ name: METRICS[metric].label, data: sorted.map((d) => d[metric]) }],
    options: {
      chart: {
        type: 'bar',
        height: 620,
        toolbar: { show: false },
        fontFamily: 'Inter, sans-serif',
        animations: { speed: 350 },
        events: {
          dataPointSelection: (event, chartContext, config) => {
            const bu = sorted[config.dataPointIndex];
            if (bu) navigate(`/admin/business-unit/${bu.bu_id}`);
          },
        },
      },
      colors: [METRICS[metric].color],
      plotOptions: { bar: { horizontal: true, borderRadius: 3, barHeight: '62%' } },
      dataLabels: { enabled: true, formatter: (v) => formatMoney(v), style: { fontSize: '10px', colors: [isDark ? '#e2e8f0' : '#1e293b'] }, offsetX: 24 },
      xaxis: { categories: sorted.map((d) => d.code), labels: { formatter: (v) => 'Ks ' + (v / 1000).toFixed(0) + 'k', style: { colors: '#94a3b8', fontSize: '11px' } } },
      yaxis: { labels: { style: { colors: isDark ? '#cbd5e1' : '#475569', fontSize: '11px', fontWeight: 600 } } },
      grid: { borderColor: isDark ? '#334155' : '#eef2f7' },
      annotations: {
        xaxis: [
          {
            x: average,
            borderColor: '#94a3b8',
            strokeDashArray: 4,
            label: {
              text: 'Avg ' + formatMoney(average),
              orientation: 'horizontal',
              style: isDark
                ? { color: '#c7d2fe', background: '#312e81', fontSize: '10px', fontWeight: 600 }
                : { color: '#4338ca', background: '#eef2ff', fontSize: '10px', fontWeight: 600 },
            },
          },
        ],
      },
      tooltip: { theme: isDark ? 'dark' : 'light', y: { formatter: (v) => formatMoney(v) } },
    },
  };

  return (
    <div className={`min-h-screen ${PAGE_BG} text-slate-800 dark:text-slate-200 antialiased flex flex-col lg:flex-row`}>
      <AdminSidebar />

      <main className="flex-1 min-w-0 px-4 sm:px-6 py-6 sm:py-8 space-y-8 fade-in">
        <header className={`${GLASS} px-5 sm:px-6 py-4 flex items-center justify-between flex-wrap gap-4`}>
          <div>
            <h1 className="font-bold text-slate-900 dark:text-slate-100 text-lg">Comparison & Analytics</h1>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">{data.today_display}</p>
          </div>
        </header>

        <section className="grid lg:grid-cols-5 gap-6">
          <div className={`lg:col-span-3 ${GLASS} p-6`}>
            <div className="mb-4 flex items-center justify-between flex-wrap gap-3">
              <div>
                <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Performance Trend</p>
                <h2 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight text-lg">Cash Flow Trend</h2>
                <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Last 14 days &middot; one business unit at a time</p>
              </div>
              <select
                value={trendBuId ?? ''}
                onChange={(e) => setTrendBuId(Number(e.target.value))}
                className="text-sm font-semibold text-slate-700 dark:text-slate-300 bg-white/70 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
              >
                {data.trend_cash_flow_by_bu.map((b) => (
                  <option key={b.bu_id} value={b.bu_id}>
                    {b.bu_name}
                  </option>
                ))}
              </select>
            </div>
            <Chart options={trendChart.options} series={trendChart.series} type="area" height={300} />
          </div>
          <div className={`lg:col-span-2 ${GLASS} p-6`}>
            <div className="mb-4">
              <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">By Business Unit</p>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight text-lg">Receivable vs Payable</h2>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Latest reported figures per BU</p>
            </div>
            <Chart options={barChart.options} series={barChart.series} type="bar" height={300} />
          </div>
        </section>

        <section className={`${GLASS} overflow-hidden`}>
          <div className="p-6 border-b border-slate-200/70 dark:border-slate-700/70 flex items-center justify-between flex-wrap gap-4">
            <div>
              <p className="text-[10.5px] font-bold uppercase tracking-[0.12em] text-brand-600 dark:text-brand-400 mb-1">Comparative Analysis</p>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 tracking-tight text-lg">Business Unit Performance Comparison</h2>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">All business units ranked by the selected metric &middot; click a row for full details</p>
            </div>
            <div className="flex items-center gap-1 bg-slate-100 dark:bg-slate-800 rounded-lg p-1 flex-wrap">
              {Object.entries(METRICS).map(([key, meta]) => (
                <button
                  key={key}
                  onClick={() => setMetric(key)}
                  className={`text-xs font-semibold px-3.5 py-1.5 rounded-md transition-colors whitespace-nowrap ${
                    metric === key ? 'bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100 shadow-sm' : 'text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-300'
                  }`}
                >
                  {meta.label}
                </button>
              ))}
            </div>
          </div>
          <div className="grid lg:grid-cols-5">
            <div className="lg:col-span-3 p-6 border-b lg:border-b-0 lg:border-r border-slate-200/70 dark:border-slate-700/70">
              <Chart options={rankingChart.options} series={rankingChart.series} type="bar" height={620} />
            </div>
            <div className="lg:col-span-2 flex flex-col min-h-0 min-w-0">
              <p className="lg:hidden px-4 pt-3 pb-1 text-[11px] font-medium text-slate-400 dark:text-slate-500 flex items-center gap-1 shrink-0">
                <svg xmlns="http://www.w3.org/2000/svg" className="w-3 h-3 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5"><path strokeLinecap="round" strokeLinejoin="round" d="M13 5l7 7-7 7M5 5l7 7-7 7" /></svg>
                Swipe to see all columns
              </p>
              <div className="max-h-[620px] overflow-y-auto overflow-x-auto">
              <table className="w-full min-w-max text-sm">
                <thead className="sticky top-0 z-[5] bg-white/90 dark:bg-slate-800/90 backdrop-blur text-slate-500 dark:text-slate-400 text-xs uppercase tracking-wider">
                  <tr>
                    <th className="px-4 py-3 text-left">Rank</th>
                    <th className="px-4 py-3 text-left">BU</th>
                    <th className="px-4 py-3 text-right">Value</th>
                    <th className="px-4 py-3 text-right">vs Avg</th>
                    <th className="px-4 py-3 text-center">Budget</th>
                    <th className="px-4 py-3 text-center">MTD Achievement %</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {sorted.map((d, i) => {
                    const delta = average !== 0 ? ((d[metric] - average) / Math.abs(average)) * 100 : 0;
                    const rankBadge = i === 0 ? 'bg-amber-500/15 text-amber-600 dark:text-amber-400' : i === sorted.length - 1 ? 'bg-slate-200 dark:bg-slate-700 text-slate-500 dark:text-slate-400' : 'bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400';
                    return (
                      <tr key={d.bu_id} className="hover:bg-slate-50 dark:hover:bg-slate-800/60 cursor-pointer transition-colors" onClick={() => navigate(`/admin/business-unit/${d.bu_id}`)}>
                        <td className="px-4 py-3">
                          <span className={`inline-flex items-center justify-center w-6 h-6 rounded-full text-[11px] font-bold ${rankBadge}`}>{i + 1}</span>
                        </td>
                        <td className="px-4 py-3">
                          <p className="font-semibold text-slate-800 dark:text-slate-200">{d.name}</p>
                        </td>
                        <td className="px-4 py-3 text-right font-semibold tabular-nums text-slate-700 dark:text-slate-300">{formatMoney(d[metric])}</td>
                        <td className={`px-4 py-3 text-right text-xs font-semibold tabular-nums ${delta >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}`}>
                          {delta >= 0 ? '+' : ''}
                          {delta.toFixed(1)}%
                        </td>
                        <td className="px-4 py-3 text-center">
                          <span className={`text-[9px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${BUDGET_BADGE_CLASS[d.budget_status] || BUDGET_BADGE_CLASS.grey}`}>
                            {BUDGET_STATUS_LABEL[d.budget_status] || BUDGET_STATUS_LABEL.grey}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-center font-semibold tabular-nums text-slate-700 dark:text-slate-300">{d.achievement_pct != null ? `${d.achievement_pct}%` : '—'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              </div>
            </div>
          </div>
        </section>
        <Footer />
      </main>
    </div>
  );
}
