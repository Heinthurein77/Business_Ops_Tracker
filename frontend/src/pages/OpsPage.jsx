import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import AdminSidebar from '../components/AdminSidebar';
import Footer from '../components/Footer';
import { GLASS, PAGE_BG } from '../utils/glassTheme';

const TABS = [
  { key: 'actions', label: 'Action Items' },
  { key: 'inventory', label: 'Inventory' },
  { key: 'purchasing', label: 'Purchasing' },
  { key: 'suppliers', label: 'Suppliers' },
  { key: 'invoices', label: 'Invoices' },
  { key: 'pos', label: 'POS' },
  { key: 'transfers', label: 'Transfers' },
  { key: 'projects', label: 'Projects' },
  { key: 'shipments', label: 'Shipments' },
  { key: 'production', label: 'Production' },
  { key: 'properties', label: 'Properties' },
  { key: 'hr', label: 'HR & Payroll' },
  { key: 'expenses', label: 'Expenses' },
  { key: 'documents', label: 'Documents' },
  { key: 'staff', label: 'Staff' },
  { key: 'customers', label: 'Customers' },
  { key: 'forecast', label: 'Forecast' },
];

const STATUS_BADGE = {
  pending: 'bg-amber-500/10 border-amber-500/20 text-amber-600 dark:text-amber-400',
  in_progress: 'bg-sky-500/10 border-sky-500/20 text-sky-600 dark:text-sky-400',
  done: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600 dark:text-emerald-400',
  approved: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600 dark:text-emerald-400',
  rejected: 'bg-red-500/10 border-red-500/20 text-red-600 dark:text-red-400',
  draft: 'bg-slate-500/10 border-slate-500/20 text-slate-600 dark:text-slate-300',
  sent: 'bg-sky-500/10 border-sky-500/20 text-sky-600 dark:text-sky-400',
  partial: 'bg-amber-500/10 border-amber-500/20 text-amber-600 dark:text-amber-400',
  overdue: 'bg-red-500/10 border-red-500/20 text-red-600 dark:text-red-400',
  ordered: 'bg-sky-500/10 border-sky-500/20 text-sky-600 dark:text-sky-400',
  received: 'bg-amber-500/10 border-amber-500/20 text-amber-600 dark:text-amber-400',
  paid: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600 dark:text-emerald-400',
  cancelled: 'bg-red-500/10 border-red-500/20 text-red-600 dark:text-red-400',
  open: 'bg-slate-500/10 border-slate-500/20 text-slate-600 dark:text-slate-300',
  completed: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600 dark:text-emerald-400',
  void: 'bg-red-500/10 border-red-500/20 text-red-600 dark:text-red-400',
  accepted: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600 dark:text-emerald-400',
  converted: 'bg-violet-500/10 border-violet-500/20 text-violet-600 dark:text-violet-400',
  in_transit: 'bg-sky-500/10 border-sky-500/20 text-sky-600 dark:text-sky-400',
  in_stock: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600 dark:text-emerald-400',
  sold: 'bg-slate-500/10 border-slate-500/20 text-slate-600 dark:text-slate-300',
  returned: 'bg-amber-500/10 border-amber-500/20 text-amber-600 dark:text-amber-400',
  defective: 'bg-red-500/10 border-red-500/20 text-red-600 dark:text-red-400',
  settled: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-600 dark:text-emerald-400',
  posted: 'bg-sky-500/10 border-sky-500/20 text-sky-600 dark:text-sky-400',
};

function money(v) {
  return `Ks ${Number(v || 0).toLocaleString()}`;
}

export default function OpsPage() {
  const [tab, setTab] = useState('actions');
  const [bus, setBus] = useState([]);
  const [buId, setBuId] = useState('');

  const [actions, setActions] = useState([]);
  const [newTitle, setNewTitle] = useState('');
  const [newDue, setNewDue] = useState('');

  const [inventory, setInventory] = useState([]);
  const [lowCount, setLowCount] = useState(0);
  const [invForm, setInvForm] = useState({ name: '', reorder_level: '', initial_stock: '', unit: 'pcs', brand: '', principal_id: '', requires_expiry: false });
  const [projects, setProjects] = useState([]);
  const [projForm, setProjForm] = useState({ name: '', project_type: 'general', customer_id: '' });
  const [projPL, setProjPL] = useState({});
  const [shipments, setShipments] = useState([]);
  const [shipForm, setShipForm] = useState({ origin: '', destination: '', vehicle_no: '', driver_name: '', shipment_date: new Date().toISOString().slice(0, 10), freight_charge: '', fuel_cost: '', toll_fees: '', driver_fees: '', customer_id: '' });
  const [boms, setBoms] = useState([]);
  const [bomForm, setBomForm] = useState({ finished_item_id: '', name: '', components: [{ component_item_id: '', quantity_per_unit: '' }] });
  const [prodOrders, setProdOrders] = useState([]);
  const [prodForm, setProdForm] = useState({ bom_id: '', quantity: '', labour_cost: '', overhead_cost: '' });
  const [units, setUnits] = useState([]);
  const [unitForm, setUnitForm] = useState({ development: '', unit_no: '', unit_type: 'apartment', size_sqft: '', asking_price: '' });
  const [leases, setLeases] = useState([]);
  const [leaseForm, setLeaseForm] = useState({ unit_id: '', tenant_id: '', monthly_rent: '', deposit_amount: '', start_date: new Date().toISOString().slice(0, 10) });
  const [sellForms, setSellForms] = useState({});
  const [expiring, setExpiring] = useState([]);
  const [batchItem, setBatchItem] = useState(null);
  const [batches, setBatches] = useState([]);
  const [batchForm, setBatchForm] = useState({ batch_no: '', expiry_date: '', quantity: '', unit_cost: '' });
  const [principalReport, setPrincipalReport] = useState(null);
  const [moveQty, setMoveQty] = useState({});
  const [transferTo, setTransferTo] = useState({});

  const [expenses, setExpenses] = useState([]);
  const [documents, setDocuments] = useState([]);
  const [docForm, setDocForm] = useState({ title: '', document_type: 'license', expiry_date: '', notes: '' });
  const [reportMonth, setReportMonth] = useState(new Date().toISOString().slice(0, 7));
  const [staff, setStaff] = useState([]);
  const [customers, setCustomers] = useState([]);
  const [forecast, setForecast] = useState(null);
  const [suppliers, setSuppliers] = useState([]);
  const [supForm, setSupForm] = useState({ name: '', contact_person: '', phone: '' });
  const [pos, setPos] = useState([]);
  const [poForm, setPoForm] = useState({ supplier_id: '', order_date: new Date().toISOString().slice(0, 10), currency: 'MMK', exchange_rate: '', lines: [{ item_name: '', quantity: '', unit_cost: '' }] });
  const [salesCustomers, setSalesCustomers] = useState([]);
  const [custForm, setCustForm] = useState({ name: '', phone: '' });
  const [invoices, setInvoices] = useState([]);
  const [invCreate, setInvCreate] = useState({ customer_id: '', invoice_date: new Date().toISOString().slice(0, 10), due_date: '', tax_rate: 5, currency: 'MMK', exchange_rate: '', project_id: '', lines: [{ description: '', quantity: 1, unit_price: '', inventory_item_id: '' }] });
  const [payAmt, setPayAmt] = useState({});
  const [agingData, setAgingData] = useState(null);
  const [employees, setEmployees] = useState([]);
  const [empForm, setEmpForm] = useState({ name: '', position: '', base_salary: '' });
  const [leaveList, setLeaveList] = useState([]);
  const [leaveForm, setLeaveForm] = useState({ employee_id: '', leave_type: 'annual', start_date: '', end_date: '' });
  const [payrollRuns, setPayrollRuns] = useState([]);
  const [payrollPeriod, setPayrollPeriod] = useState(new Date().toISOString().slice(0, 7));
  const [lineEdits, setLineEdits] = useState({});
  // ERP completion state
  const [posSales, setPosSales] = useState([]);
  const [posSummary, setPosSummary] = useState(null);
  const [posItems, setPosItems] = useState([]);
  const [posCart, setPosCart] = useState([]);
  const [posPay, setPosPay] = useState({ payment_method: 'cash', tax_rate: 0, customer_name: '' });
  const [quotations, setQuotations] = useState([]);
  const [quoteForm, setQuoteForm] = useState({ customer_id: '', quote_date: new Date().toISOString().slice(0, 10), valid_until: '', tax_rate: 5, lines: [{ description: '', quantity: 1, unit_price: '', inventory_item_id: '' }] });
  const [salesReturns, setSalesReturns] = useState([]);
  const [returnFor, setReturnFor] = useState(null); // invoice id with the return form open
  const [returnQty, setReturnQty] = useState({});
  const [purchaseReturns, setPurchaseReturns] = useState([]);
  const [poReturnFor, setPoReturnFor] = useState(null);
  const [poReturnQty, setPoReturnQty] = useState({});
  const [transfers, setTransfers] = useState([]);
  const [transferForm, setTransferForm] = useState({ to_business_unit_id: '', lines: [{ inventory_item_id: '', quantity: '' }] });
  const [serialItem, setSerialItem] = useState(null);
  const [serials, setSerials] = useState([]);
  const [serialForm, setSerialForm] = useState({ text: '', warranty_until: '' });
  const [serialQuery, setSerialQuery] = useState('');
  const [serialResults, setSerialResults] = useState(null);
  const [advances, setAdvances] = useState([]);
  const [advForm, setAdvForm] = useState({ employee_id: '', amount: '', monthly_deduction: '', reason: '' });
  const [attMonth, setAttMonth] = useState(new Date().toISOString().slice(0, 7));
  const [attSummary, setAttSummary] = useState([]);
  const [attRecords, setAttRecords] = useState([]);
  const [attEdits, setAttEdits] = useState({});
  const [leaveBals, setLeaveBals] = useState([]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    api.adminOverview().then((d) => setBus(d.operations || [])).catch(() => {});
  }, []);

  const load = useCallback(async () => {
    setError('');
    try {
      const bu = buId ? Number(buId) : undefined;
      if (tab === 'actions') setActions((await api.listActionItems({ business_unit_id: bu })).items);
      if (tab === 'inventory') {
        const d = await api.listInventory({ business_unit_id: bu });
        setInventory(d.items);
        setLowCount(d.low_count);
        setExpiring((await api.modExpiring(bu).catch(() => ({ items: [] }))).items);
        setSuppliers((await api.listSuppliers(bu)).items);
      }
      if (tab === 'expenses') setExpenses((await api.listExpenses({ business_unit_id: bu })).items);
      if (tab === 'documents') setDocuments((await api.listDocuments({ business_unit_id: bu })).items);
      if (tab === 'staff') setStaff((await api.listStaff({ business_unit_id: bu, days: 14 })).items);
      if (tab === 'customers') setCustomers((await api.listCustomers({ business_unit_id: bu, days: 14 })).items);
      if (tab === 'forecast') setForecast(await api.forecast(bu));
      if (tab === 'suppliers') setSuppliers((await api.listSuppliers(bu)).items);
      if (tab === 'purchasing') {
        setPos((await api.listPOs(bu)).items);
        setSuppliers((await api.listSuppliers(bu)).items);
        setPurchaseReturns((await api.listPurchaseReturns(bu).catch(() => ({ items: [] }))).items);
      }
      if (tab === 'invoices') {
        setInvoices((await api.listInvoices(bu)).items);
        setSalesCustomers((await api.listSalesCustomers(bu)).items);
        setAgingData(await api.aging(bu));
        setProjects((await api.modProjects(bu).catch(() => ({ items: [] }))).items);
        setInventory((await api.listInventory({ business_unit_id: bu }).catch(() => ({ items: [] }))).items);
        setPrincipalReport(await api.modPrincipalReport(bu).catch(() => null));
        setQuotations((await api.listQuotations(bu).catch(() => ({ items: [] }))).items);
        setSalesReturns((await api.listSalesReturns(bu).catch(() => ({ items: [] }))).items);
      }
      if (tab === 'pos') {
        setPosItems((await api.listInventory({ business_unit_id: bu }).catch(() => ({ items: [] }))).items);
        setPosSales((await api.listPosSales(bu).catch(() => ({ items: [] }))).items);
        setPosSummary(await api.posDailySummary(bu).catch(() => null));
      }
      if (tab === 'transfers') {
        setTransfers((await api.modStockTransfers({}).catch(() => ({ items: [] }))).items);
        setInventory((await api.listInventory({ business_unit_id: bu }).catch(() => ({ items: [] }))).items);
      }
      if (tab === 'projects') {
        setProjects((await api.modProjects(bu)).items);
        setSalesCustomers((await api.listSalesCustomers(bu)).items);
      }
      if (tab === 'shipments') {
        setShipments((await api.modShipments(bu)).items);
        setSalesCustomers((await api.listSalesCustomers(bu)).items);
      }
      if (tab === 'production') {
        setBoms((await api.modBoms(bu)).items);
        setProdOrders((await api.modProduction(bu)).items);
        setInventory((await api.listInventory({ business_unit_id: bu })).items);
      }
      if (tab === 'properties') {
        setUnits((await api.modUnits(bu)).items);
        setLeases((await api.modLeases(bu)).items);
        setSalesCustomers((await api.listSalesCustomers(bu)).items);
      }
      if (tab === 'hr') {
        setEmployees((await api.listEmployees(bu)).items);
        setLeaveList((await api.listLeave(bu)).items);
        setPayrollRuns((await api.listPayroll(bu)).items);
        setAdvances((await api.listAdvances(bu).catch(() => ({ items: [] }))).items);
        setLeaveBals((await api.leaveBalances(bu).catch(() => ({ items: [] }))).items);
        const att = await api.listAttendance(bu, attMonth).catch(() => ({ items: [], summary: [] }));
        setAttRecords(att.items);
        setAttSummary(att.summary);
      }
    } catch (e) {
      setError(e.message || 'Failed to load');
    }
  }, [tab, buId, attMonth]);

  useEffect(() => { load(); }, [load]);

  async function addEmployee(e) {
    e.preventDefault();
    if (!empForm.name.trim()) return;
    if (!buId) { setError('Select a specific business unit first (not All Business Units)'); return; }
    try {
      await api.createEmployee({ business_unit_id: Number(buId), name: empForm.name.trim(), position: empForm.position || undefined, base_salary: Number(empForm.base_salary) || 0 });
      setEmpForm({ name: '', position: '', base_salary: '' });
      load();
    } catch (err) { setError(err.message); }
  }

  async function submitLeave(e) {
    e.preventDefault();
    if (!leaveForm.employee_id || !leaveForm.start_date || !leaveForm.end_date) return;
    try {
      await api.createLeave({ employee_id: Number(leaveForm.employee_id), leave_type: leaveForm.leave_type, start_date: leaveForm.start_date, end_date: leaveForm.end_date });
      setLeaveForm({ employee_id: '', leave_type: 'annual', start_date: '', end_date: '' });
      load();
    } catch (err) { setError(err.message); }
  }

  async function generatePayroll() {
    if (!buId) return;
    try {
      await api.createPayroll({ business_unit_id: Number(buId), period: payrollPeriod });
      setNotice(`Payroll draft for ${payrollPeriod} generated.`);
      setTimeout(() => setNotice(''), 4000);
      load();
    } catch (err) { setError(err.message); }
  }

  async function createQuotation(e) {
    e.preventDefault();
    if (!buId || !quoteForm.customer_id) { setError('Select a business unit and a customer first'); return; }
    try {
      await api.createQuotation({
        business_unit_id: Number(buId), customer_id: Number(quoteForm.customer_id),
        quote_date: quoteForm.quote_date, valid_until: quoteForm.valid_until || undefined,
        tax_rate: Number(quoteForm.tax_rate) || 0,
        lines: quoteForm.lines.filter((l) => l.description && l.unit_price !== '').map((l) => ({
          description: l.description, quantity: Number(l.quantity) || 1, unit_price: Number(l.unit_price),
          inventory_item_id: l.inventory_item_id ? Number(l.inventory_item_id) : undefined,
        })),
      });
      setQuoteForm({ customer_id: '', quote_date: new Date().toISOString().slice(0, 10), valid_until: '', tax_rate: 5, lines: [{ description: '', quantity: 1, unit_price: '', inventory_item_id: '' }] });
      load();
    } catch (err) { setError(err.message); }
  }

  async function completePosSale() {
    if (!buId) { setError('Select a specific business unit first (not All Business Units)'); return; }
    if (posCart.length === 0) return;
    try {
      const sale = await api.createPosSale({
        business_unit_id: Number(buId), payment_method: posPay.payment_method,
        tax_rate: Number(posPay.tax_rate) || 0, customer_name: posPay.customer_name || undefined,
        lines: posCart.map((l) => ({ inventory_item_id: l.item_id, quantity: l.quantity, unit_price: l.unit_price, serial_nos: l.serial_nos && l.serial_nos.length ? l.serial_nos : undefined })),
      });
      setPosCart([]);
      setNotice(`POS sale #${sale.id} completed — ${money(sale.total)}. Do not re-enter it in the CEO daily report.`);
      setTimeout(() => setNotice(''), 5000);
      load();
    } catch (err) { setError(err.message); }
  }

  async function submitSalesReturn(inv) {
    const lines = inv.lines
      .map((l) => ({ invoice_line_id: l.id, quantity: Number(returnQty[l.id]) || 0 }))
      .filter((l) => l.quantity > 0);
    if (lines.length === 0) { setError('Enter a return quantity on at least one line'); return; }
    try {
      const r = await api.createSalesReturn({ invoice_id: inv.id, lines });
      setNotice(`Credit note ${r.return_no} posted — ${money(r.total)} credited to ${inv.invoice_no}.`);
      setTimeout(() => setNotice(''), 4000);
      setReturnFor(null);
      setReturnQty({});
      load();
    } catch (err) { setError(err.message); }
  }

  async function submitPurchaseReturn(po) {
    const lines = po.lines
      .map((l) => ({ po_line_id: l.id, quantity: Number(poReturnQty[l.id]) || 0 }))
      .filter((l) => l.quantity > 0);
    if (lines.length === 0) { setError('Enter a return quantity on at least one line'); return; }
    try {
      const r = await api.createPurchaseReturn({ po_id: po.id, lines });
      setNotice(`Debit note ${r.return_no} posted — ${money(r.total)} returned to supplier.`);
      setTimeout(() => setNotice(''), 4000);
      setPoReturnFor(null);
      setPoReturnQty({});
      load();
    } catch (err) { setError(err.message); }
  }

  async function createTransfer(e) {
    e.preventDefault();
    if (!buId || !transferForm.to_business_unit_id) { setError('Select source BU (page filter) and a destination BU'); return; }
    const lines = transferForm.lines.filter((l) => l.inventory_item_id && Number(l.quantity) > 0)
      .map((l) => ({ inventory_item_id: Number(l.inventory_item_id), quantity: Number(l.quantity) }));
    if (lines.length === 0) { setError('Add at least one transfer line'); return; }
    try {
      await api.modCreateTransfer({ from_business_unit_id: Number(buId), to_business_unit_id: Number(transferForm.to_business_unit_id), lines });
      setTransferForm({ to_business_unit_id: '', lines: [{ inventory_item_id: '', quantity: '' }] });
      load();
    } catch (err) { setError(err.message); }
  }

  async function submitAdvance(e) {
    e.preventDefault();
    if (!advForm.employee_id || !advForm.amount) return;
    try {
      await api.createAdvance({
        employee_id: Number(advForm.employee_id), amount: Number(advForm.amount),
        monthly_deduction: Number(advForm.monthly_deduction) || Number(advForm.amount),
        reason: advForm.reason || undefined,
      });
      setAdvForm({ employee_id: '', amount: '', monthly_deduction: '', reason: '' });
      load();
    } catch (err) { setError(err.message); }
  }

  async function saveAttendance() {
    const today = new Date().toISOString().slice(0, 10);
    const records = Object.entries(attEdits)
      .filter(([, st]) => st)
      .map(([empId, st]) => ({ employee_id: Number(empId), record_date: today, status: st }));
    if (records.length === 0) { setError('Set a status for at least one employee'); return; }
    try {
      await api.bulkAttendance({ records });
      setAttEdits({});
      setNotice(`Attendance saved for ${records.length} employee(s) — ${today}.`);
      setTimeout(() => setNotice(''), 4000);
      load();
    } catch (err) { setError(err.message); }
  }

  async function addSalesCustomer(e) {
    e.preventDefault();
    if (!custForm.name.trim() || !buId) return;
    try {
      await api.createSalesCustomer({ business_unit_id: Number(buId), name: custForm.name.trim(), phone: custForm.phone || undefined });
      setCustForm({ name: '', phone: '' });
      load();
    } catch (err) { setError(err.message); }
  }

  async function createInvoice(e) {
    e.preventDefault();
    const lines = invCreate.lines.filter((l) => l.description.trim()).map((l) => ({ description: l.description.trim(), quantity: Number(l.quantity) || 1, unit_price: Number(l.unit_price) || 0, inventory_item_id: l.inventory_item_id ? Number(l.inventory_item_id) : undefined }));
    if (!invCreate.customer_id || lines.length === 0 || !buId) { setError('Pick a customer and add at least one line'); return; }
    try {
      await api.createInvoice({ business_unit_id: Number(buId), customer_id: Number(invCreate.customer_id), invoice_date: invCreate.invoice_date, due_date: invCreate.due_date || undefined, tax_rate: Number(invCreate.tax_rate) || 0, currency: invCreate.currency, exchange_rate: invCreate.exchange_rate === '' ? undefined : Number(invCreate.exchange_rate), project_id: invCreate.project_id ? Number(invCreate.project_id) : undefined, lines });
      setInvCreate({ customer_id: '', invoice_date: new Date().toISOString().slice(0, 10), due_date: '', tax_rate: 5, currency: 'MMK', exchange_rate: '', project_id: '', lines: [{ description: '', quantity: 1, unit_price: '', inventory_item_id: '' }] });
      setNotice('Invoice created (draft). Send it to post to the ledger.');
      setTimeout(() => setNotice(''), 4000);
      load();
    } catch (err) { setError(err.message); }
  }

  async function addSupplier(e) {
    e.preventDefault();
    if (!supForm.name.trim()) return;
    try {
      await api.createSupplier({ name: supForm.name.trim(), contact_person: supForm.contact_person || undefined, phone: supForm.phone || undefined, business_unit_id: buId ? Number(buId) : undefined });
      setSupForm({ name: '', contact_person: '', phone: '' });
      load();
    } catch (err) { setError(err.message); }
  }

  async function createPO(e) {
    e.preventDefault();
    const lines = poForm.lines.filter((l) => l.item_name.trim()).map((l) => ({ item_name: l.item_name.trim(), quantity: Number(l.quantity) || 0, unit_cost: Number(l.unit_cost) || 0 }));
    if (!poForm.supplier_id || lines.length === 0 || !buId) { setError('Pick a supplier and add at least one line'); return; }
    try {
      await api.createPO({ business_unit_id: Number(buId), supplier_id: Number(poForm.supplier_id), order_date: poForm.order_date, currency: poForm.currency, exchange_rate: poForm.exchange_rate === '' ? undefined : Number(poForm.exchange_rate), lines });
      setPoForm({ supplier_id: '', order_date: new Date().toISOString().slice(0, 10), lines: [{ item_name: '', quantity: '', unit_cost: '' }] });
      setNotice('Purchase order created (draft).');
      setTimeout(() => setNotice(''), 4000);
      load();
    } catch (err) { setError(err.message); }
  }

  async function addAction(e) {
    e.preventDefault();
    if (!newTitle.trim()) return;
    try {
      await api.createActionItem({ business_unit_id: buId ? Number(buId) : undefined, title: newTitle.trim(), due_date: newDue || undefined });
      setNewTitle('');
      setNewDue('');
      load();
    } catch (err) { setError(err.message); }
  }

  async function setActionStatus(item, status) {
    await api.updateActionItem(item.id, { status });
    load();
  }

  async function addInventory(e) {
    e.preventDefault();
    if (!invForm.name.trim() || !buId) { setError('Pick a business unit and enter an item name'); return; }
    try {
      await api.createInventoryItem({
        business_unit_id: Number(buId),
        name: invForm.name.trim(),
        reorder_level: Number(invForm.reorder_level) || 0,
        initial_stock: Number(invForm.initial_stock) || 0,
        unit: invForm.unit || 'pcs',
        brand: invForm.brand || undefined,
        principal_id: invForm.principal_id ? Number(invForm.principal_id) : undefined,
        requires_expiry: invForm.requires_expiry,
      });
      setInvForm({ name: '', reorder_level: '', initial_stock: '', unit: 'pcs' });
      load();
    } catch (err) { setError(err.message); }
  }

  async function recordMove(item, type, postCogs = false) {
    const qty = Number(moveQty[item.id]);
    if (!qty) return;
    try {
      await api.recordStockMovement(item.id, { movement_type: type, quantity: qty, post_cogs: postCogs });
    } catch (err) { setError(err.message); }
    setMoveQty((m) => ({ ...m, [item.id]: '' }));
    load();
  }

  async function transferItem(item) {
    const qty = Number(moveQty[item.id]);
    const dest = transferTo[item.id];
    if (!qty || !dest) { setError('Enter a quantity and pick a destination BU to transfer'); return; }
    try {
      await api.transferStock(item.id, { to_business_unit_id: Number(dest), quantity: qty });
      setNotice(`Transferred ${qty} × ${item.name}.`);
      setTimeout(() => setNotice(''), 4000);
    } catch (err) { setError(err.message); }
    load();
  }

  async function review(exp, decision) {
    await api.reviewExpense(exp.id, { decision });
    load();
  }

  async function remind() {
    const d = await api.remindMissingReports();
    setNotice(`Reminder sent to ${d.notified} CEO(s) who have not reported today.`);
    setTimeout(() => setNotice(''), 5000);
  }

  async function addDocument(e) {
    e.preventDefault();
    if (!docForm.title.trim() || !docForm.expiry_date || !buId) {
      setError('Pick a business unit, enter a title and an expiry date');
      return;
    }
    try {
      await api.createDocument({
        business_unit_id: Number(buId),
        title: docForm.title.trim(),
        document_type: docForm.document_type,
        expiry_date: docForm.expiry_date,
        notes: docForm.notes || undefined,
      });
      setDocForm({ title: '', document_type: 'license', expiry_date: '', notes: '' });
      load();
    } catch (err) { setError(err.message); }
  }

  async function checkExpiry() {
    try {
      const d = await api.checkDocumentExpiry();
      setNotice(`Expiry check done — ${d.checked} document(s) expiring soon, ${d.notified} notification(s) sent.`);
      setTimeout(() => setNotice(''), 5000);
    } catch (err) { setError(err.message); }
  }

  const buName = (id) => bus.find((b) => b.bu_id === id)?.bu_name || `BU ${id}`;

  return (
    <div className={`min-h-screen ${PAGE_BG} text-slate-800 dark:text-slate-200 antialiased flex flex-col lg:flex-row`}>
      <AdminSidebar />
      <main className="flex-1 min-w-0 px-4 sm:px-6 py-6 sm:py-8 space-y-6 fade-in">
        <header className={`${GLASS} px-5 sm:px-6 py-4 flex items-center justify-between flex-wrap gap-4`}>
          <div>
            <h1 className="font-bold text-slate-900 dark:text-slate-100 text-xl tracking-tight">Operations Center</h1>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Action items, inventory, purchasing, invoices, HR, expenses, documents, staff, customers & forecast</p>
          </div>
          <div className="flex items-center gap-3 flex-wrap">
            <select value={buId} onChange={(e) => setBuId(e.target.value)} className="text-sm font-semibold bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2">
              <option value="">All Business Units</option>
              {bus.map((b) => <option key={b.bu_id} value={b.bu_id}>{b.bu_name}</option>)}
            </select>
            <button onClick={remind} className="whitespace-nowrap text-xs font-semibold bg-amber-500/10 border border-amber-500/20 text-amber-600 dark:text-amber-400 px-3.5 py-2 rounded-lg hover:bg-amber-500/20 transition-colors">
              Remind missing reports
            </button>
            <div className="flex items-center gap-1.5 flex-wrap">
              <input type="month" value={reportMonth} onChange={(e) => setReportMonth(e.target.value)} className="text-xs font-semibold bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg px-2.5 py-2" />
              <a href={api.monthlyReportUrl(reportMonth)} className="whitespace-nowrap text-xs font-semibold bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 px-3.5 py-2 rounded-lg hover:opacity-90 transition-opacity">
                Monthly report (PDF)
              </a>
              <a href={api.opsExportUrl(tab === 'forecast' ? 'actions' : tab, buId)} className="whitespace-nowrap text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white px-3.5 py-2 rounded-lg transition-colors">
                Export Excel
              </a>
            </div>
          </div>
        </header>

        {notice && <div className="text-sm font-medium text-emerald-700 dark:text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-4 py-2.5">{notice}</div>}
        {error && <div className="text-sm font-medium text-red-700 dark:text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-2.5">{error}</div>}

        <div className="tab-track flex-wrap w-fit">
          {TABS.map((t) => (
            <button key={t.key} onClick={() => setTab(t.key)} className={`tab-pill ${tab === t.key ? 'tab-pill-active' : ''}`}>
              {t.label}
              {t.key === 'inventory' && lowCount > 0 && <span className="ml-1.5 text-[10px] font-bold bg-red-500 text-white rounded-full px-1.5 py-0.5">{lowCount}</span>}
              {t.key === 'documents' && documents.filter((d) => d.days_left !== null && d.days_left <= 30).length > 0 && <span className="ml-1.5 text-[10px] font-bold bg-red-500 text-white rounded-full px-1.5 py-0.5">{documents.filter((d) => d.days_left !== null && d.days_left <= 30).length}</span>}
              {t.key === 'expenses' && expenses.filter((e) => e.status === 'pending').length > 0 && <span className="ml-1.5 text-[10px] font-bold bg-amber-500 text-white rounded-full px-1.5 py-0.5">{expenses.filter((e) => e.status === 'pending').length}</span>}
            </button>
          ))}
        </div>

        {tab === 'actions' && (
          <section className={`${GLASS} p-6`}>
            <form onSubmit={addAction} className="flex flex-wrap gap-3 mb-5">
              <input value={newTitle} onChange={(e) => setNewTitle(e.target.value)} placeholder="New action item…" className="flex-1 min-w-[220px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500" />
              <input type="date" value={newDue} onChange={(e) => setNewDue(e.target.value)} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm" />
              <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Add</button>
            </form>
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {actions.map((a) => (
                <li key={a.id} className="py-3 flex items-center justify-between gap-3 flex-wrap">
                  <div className="min-w-0">
                    <p className={`text-sm font-semibold text-slate-800 dark:text-slate-200 ${a.status === 'done' ? 'line-through opacity-60' : ''}`}>{a.title}</p>
                    <p className="text-xs text-slate-400 dark:text-slate-500">{buName(a.business_unit_id)}{a.due_date ? ` · due ${a.due_date}` : ''}{a.assigned_to_name ? ` · ${a.assigned_to_name}` : ''}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE[a.status]}`}>{a.status.replace('_', ' ')}</span>
                    {a.status !== 'done' && <button onClick={() => setActionStatus(a, 'in_progress')} className="text-xs font-semibold text-sky-600 dark:text-sky-400 hover:underline">Start</button>}
                    {a.status !== 'done' && <button onClick={() => setActionStatus(a, 'done')} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Done</button>}
                    <button onClick={async () => { await api.deleteActionItem(a.id); load(); }} className="text-xs font-semibold text-red-500 hover:underline">Delete</button>
                  </div>
                </li>
              ))}
              {actions.length === 0 && <li className="py-8 text-center text-sm text-slate-400">No action items yet</li>}
            </ul>
          </section>
        )}

        {tab === 'inventory' && (
          <section className={`${GLASS} p-6`}>
            <form onSubmit={addInventory} className="flex flex-wrap gap-3 mb-5">
              <input value={invForm.name} onChange={(e) => setInvForm({ ...invForm, name: e.target.value })} placeholder="Item name (select a BU first)" className="flex-1 min-w-[200px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500" />
              <input type="number" value={invForm.initial_stock} onChange={(e) => setInvForm({ ...invForm, initial_stock: e.target.value })} placeholder="Stock" className="w-24 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
              <input type="number" value={invForm.reorder_level} onChange={(e) => setInvForm({ ...invForm, reorder_level: e.target.value })} placeholder="Reorder at" className="w-28 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
              <input value={invForm.unit} onChange={(e) => setInvForm({ ...invForm, unit: e.target.value })} placeholder="Unit" className="w-20 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
              <input value={invForm.brand} onChange={(e) => setInvForm({ ...invForm, brand: e.target.value })} placeholder="Brand (optional)" className="w-36 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
              <select value={invForm.principal_id} onChange={(e) => setInvForm({ ...invForm, principal_id: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                <option value="">Principal…</option>
                {suppliers.map((sp) => <option key={sp.id} value={sp.id}>{sp.name}</option>)}
              </select>
              <label className="flex items-center gap-2 text-sm text-slate-500 self-center"><input type="checkbox" checked={invForm.requires_expiry} onChange={(e) => setInvForm({ ...invForm, requires_expiry: e.target.checked })} className="w-4 h-4 accent-brand-600" /> Expiry tracked</label>
              <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Add item</button>
            </form>
            <div className="flex flex-wrap gap-2 mb-5 items-center">
              <input value={serialQuery} onChange={(e) => setSerialQuery(e.target.value)} placeholder="Serial / IMEI lookup — e.g. 356938035643809" className="flex-1 min-w-[240px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm" />
              <button onClick={async () => { if (!serialQuery.trim()) return; try { setSerialResults((await api.lookupSerial(serialQuery.trim())).items); } catch (err) { setError(err.message); } }} className="bg-slate-700 hover:bg-slate-800 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Look up</button>
              {serialResults !== null && (serialResults.length === 0
                ? <span className="text-sm text-slate-400">Not registered anywhere</span>
                : serialResults.map((s) => (
                  <span key={s.id} className="text-sm text-slate-600 dark:text-slate-300">{s.item_name} · {s.bu_name} · <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE[s.status] || STATUS_BADGE.open}`}>{s.status.replace('_', ' ')}</span>{s.warranty_until ? ` · warranty until ${s.warranty_until}` : ''}</span>
                )))}
            </div>
            {expiring.length > 0 && (
              <div className="mb-5 rounded-xl border border-amber-300/60 bg-amber-50 dark:bg-amber-500/10 px-4 py-3">
                <p className="text-sm font-bold text-amber-700 dark:text-amber-300">⏰ {expiring.length} batch(es) expiring within 30 days</p>
                <p className="text-xs text-amber-600 dark:text-amber-400 mt-1">{expiring.slice(0, 6).map((b) => `${b.item_name} · ${b.batch_no} · ${b.expiry_date} (${b.days_to_expiry}d)`).join('  ·  ')}</p>
              </div>
            )}
            <div className="overflow-x-auto">
              <table className="w-full text-sm min-w-[640px]">
                <thead className="text-left text-xs uppercase tracking-wider text-slate-400 dark:text-slate-500">
                  <tr><th className="py-2 pr-3">Item</th><th className="py-2 pr-3">BU</th><th className="py-2 pr-3 text-right">Stock</th><th className="py-2 pr-3 text-right">Avg Cost</th><th className="py-2 pr-3 text-right">Stock Value</th><th className="py-2 pr-3 text-right">Reorder at</th><th className="py-2">Move stock</th></tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {inventory.map((i) => (
                    <tr key={i.id}>
                      <td className="py-2.5 pr-3 font-semibold text-slate-800 dark:text-slate-200">{i.name} {i.brand && <span className="block text-[11px] font-normal text-slate-400">{i.brand}</span>} {i.is_low && <span className="ml-1 text-[10px] font-bold bg-red-500/10 border border-red-500/20 text-red-600 dark:text-red-400 rounded-full px-2 py-0.5">LOW</span>}</td>
                      <td className="py-2.5 pr-3 text-slate-500 dark:text-slate-400">{buName(i.business_unit_id)}</td>
                      <td className="py-2.5 pr-3 text-right tabular-nums font-semibold">{i.current_stock} {i.unit}</td>
                      <td className="py-2.5 pr-3 text-right tabular-nums text-slate-500">{i.unit_cost ? money(i.unit_cost) : '—'}</td>
                      <td className="py-2.5 pr-3 text-right tabular-nums text-slate-500">{i.unit_cost ? money(i.current_stock * i.unit_cost) : '—'}</td>
                      <td className="py-2.5 pr-3 text-right tabular-nums text-slate-500">{i.reorder_level}</td>
                      <td className="py-2.5">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <input type="number" value={moveQty[i.id] || ''} onChange={(e) => setMoveQty((m) => ({ ...m, [i.id]: e.target.value }))} placeholder="Qty" className="w-20 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-2 py-1.5 text-xs" />
                          <button onClick={() => recordMove(i, 'in')} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">+ In</button>
                          <button onClick={() => recordMove(i, 'out')} className="text-xs font-semibold text-red-500 hover:underline">− Out</button>
                          <button onClick={() => recordMove(i, 'out', true)} className="text-xs font-semibold text-violet-600 dark:text-violet-400 hover:underline" title="Stock out as a sale — posts COGS to the journal at average cost">Sold (COGS)</button>
                          <select value={transferTo[i.id] || ''} onChange={(e) => setTransferTo((t) => ({ ...t, [i.id]: e.target.value }))} className="text-xs rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-1.5 py-1.5">
                            <option value="">Transfer to…</option>
                            {bus.filter((b) => b.bu_id !== i.business_unit_id).map((b) => <option key={b.bu_id} value={b.bu_id}>{b.bu_name}</option>)}
                          </select>
                          <button onClick={() => transferItem(i)} className="text-xs font-semibold text-sky-600 dark:text-sky-400 hover:underline">Transfer</button>
                          <button onClick={async () => { if (batchItem === i.id) { setBatchItem(null); } else { setBatchItem(i.id); setBatches((await api.modBatches(i.id)).items); } }} className="text-xs font-semibold text-amber-600 dark:text-amber-400 hover:underline">Batches</button>
                          <button onClick={async () => { if (serialItem === i.id) { setSerialItem(null); } else { setSerialItem(i.id); setSerials((await api.listSerials(i.id)).items); } }} className="text-xs font-semibold text-indigo-600 dark:text-indigo-400 hover:underline">Serials</button>
                          <button onClick={async () => { await api.deleteInventoryItem(i.id); load(); }} className="text-xs font-semibold text-slate-400 hover:underline">Remove</button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {inventory.length === 0 && <tr><td colSpan={7} className="py-8 text-center text-slate-400">No inventory items yet</td></tr>}
                </tbody>
              </table>
            </div>
            {batchItem && (
              <div className="mt-5 border-t border-slate-100 dark:border-slate-800 pt-4">
                <h3 className="text-sm font-bold text-slate-800 dark:text-slate-200 mb-3">Batches — {inventory.find((x) => x.id === batchItem)?.name}</h3>
                <form onSubmit={async (e) => { e.preventDefault(); try { await api.modReceiveBatch(batchItem, { batch_no: batchForm.batch_no, expiry_date: batchForm.expiry_date || undefined, quantity: Number(batchForm.quantity), unit_cost: Number(batchForm.unit_cost) || 0 }); setBatchForm({ batch_no: '', expiry_date: '', quantity: '', unit_cost: '' }); setBatches((await api.modBatches(batchItem)).items); load(); } catch (err) { setError(err.message); } }} className="flex flex-wrap gap-2 mb-3">
                  <input value={batchForm.batch_no} onChange={(e) => setBatchForm({ ...batchForm, batch_no: e.target.value })} placeholder="Batch / lot no" className="w-40 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-sm" />
                  <input type="date" value={batchForm.expiry_date} onChange={(e) => setBatchForm({ ...batchForm, expiry_date: e.target.value })} title="Expiry date" className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-sm" />
                  <input type="number" value={batchForm.quantity} onChange={(e) => setBatchForm({ ...batchForm, quantity: e.target.value })} placeholder="Qty" className="w-24 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-sm" />
                  <input type="number" value={batchForm.unit_cost} onChange={(e) => setBatchForm({ ...batchForm, unit_cost: e.target.value })} placeholder="Unit cost" className="w-28 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-sm" />
                  <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2 rounded-lg transition-colors">Receive batch</button>
                </form>
                <ul className="divide-y divide-slate-100 dark:divide-slate-800 text-sm">
                  {batches.map((b) => (
                    <li key={b.id} className="py-2 flex justify-between flex-wrap gap-2">
                      <span className="font-medium">{b.batch_no} <span className="text-slate-400 font-normal">· {b.quantity} left · cost {money(b.unit_cost)}</span></span>
                      <span className={b.days_to_expiry !== null && b.days_to_expiry <= 30 ? 'text-amber-600 dark:text-amber-400 font-semibold text-xs' : 'text-slate-400 text-xs'}>{b.expiry_date ? `expires ${b.expiry_date} (${b.days_to_expiry}d)` : 'no expiry'}</span>
                    </li>
                  ))}
                  {batches.length === 0 && <li className="py-4 text-center text-slate-400 text-sm">No batches with stock — receive the first batch above</li>}
                </ul>
              </div>
            )}
            {serialItem && (
              <div className="mt-5 border-t border-slate-100 dark:border-slate-800 pt-4">
                <h3 className="text-sm font-bold text-slate-800 dark:text-slate-200 mb-3">Serials / IMEI — {inventory.find((x) => x.id === serialItem)?.name}</h3>
                <form onSubmit={async (e) => { e.preventDefault(); const nos = serialForm.text.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean); if (!nos.length) return; try { await api.addSerials(serialItem, { serial_nos: nos, warranty_until: serialForm.warranty_until || undefined }); setSerialForm({ text: '', warranty_until: '' }); setSerials((await api.listSerials(serialItem)).items); } catch (err) { setError(err.message); } }} className="flex flex-wrap gap-2 mb-3">
                  <textarea value={serialForm.text} onChange={(e) => setSerialForm({ ...serialForm, text: e.target.value })} placeholder="One serial per line (or comma separated)" rows={2} className="flex-1 min-w-[260px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-sm" />
                  <input type="date" value={serialForm.warranty_until} onChange={(e) => setSerialForm({ ...serialForm, warranty_until: e.target.value })} title="Warranty until" className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-sm self-start" />
                  <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2 rounded-lg transition-colors self-start">Register serials</button>
                </form>
                <ul className="divide-y divide-slate-100 dark:divide-slate-800 text-sm">
                  {serials.map((s) => (
                    <li key={s.id} className="py-2 flex items-center justify-between flex-wrap gap-2">
                      <span className="font-medium tabular-nums">{s.serial_no} {s.warranty_until && <span className="text-slate-400 font-normal text-xs">· warranty until {s.warranty_until}</span>}</span>
                      <span className="flex items-center gap-2">
                        <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE[s.status] || STATUS_BADGE.open}`}>{s.status.replace('_', ' ')}</span>
                        {s.status !== 'defective' && <button onClick={async () => { await api.setSerialStatus(s.id, 'defective'); setSerials((await api.listSerials(serialItem)).items); }} className="text-xs font-semibold text-red-500 hover:underline">Mark defective</button>}
                        {s.status !== 'in_stock' && <button onClick={async () => { await api.setSerialStatus(s.id, 'in_stock'); setSerials((await api.listSerials(serialItem)).items); }} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Back in stock</button>}
                      </span>
                    </li>
                  ))}
                  {serials.length === 0 && <li className="py-4 text-center text-slate-400 text-sm">No serials registered for this item yet</li>}
                </ul>
              </div>
            )}
          </section>
        )}

        {tab === 'purchasing' && (
          <section className={`${GLASS} p-6`}>
            <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">New Purchase Order</h2>
            <form onSubmit={createPO} className="space-y-3 mb-6">
              <div className="flex flex-wrap gap-3">
                <select value={poForm.supplier_id} onChange={(e) => setPoForm({ ...poForm, supplier_id: e.target.value })} className="flex-1 min-w-[200px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                  <option value="">Select supplier…</option>
                  {suppliers.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.scope})</option>)}
                </select>
                <input type="date" value={poForm.order_date} onChange={(e) => setPoForm({ ...poForm, order_date: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <select value={poForm.currency} onChange={(e) => setPoForm({ ...poForm, currency: e.target.value })} title="Currency" className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                  <option value="MMK">MMK</option>
                  <option value="USD">USD</option>
                </select>
                {poForm.currency !== 'MMK' && <input type="number" value={poForm.exchange_rate} onChange={(e) => setPoForm({ ...poForm, exchange_rate: e.target.value })} placeholder="Ks per 1 USD (blank = latest)" title="Exchange rate" className="w-52 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />}
              </div>
              {poForm.lines.map((l, idx) => (
                <div key={idx} className="flex flex-wrap gap-2">
                  <input value={l.item_name} onChange={(e) => { const lines = [...poForm.lines]; lines[idx].item_name = e.target.value; setPoForm({ ...poForm, lines }); }} placeholder="Item name" className="flex-1 min-w-[180px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm" />
                  <input type="number" value={l.quantity} onChange={(e) => { const lines = [...poForm.lines]; lines[idx].quantity = e.target.value; setPoForm({ ...poForm, lines }); }} placeholder="Qty" className="w-28 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                  <input type="number" value={l.unit_cost} onChange={(e) => { const lines = [...poForm.lines]; lines[idx].unit_cost = e.target.value; setPoForm({ ...poForm, lines }); }} placeholder="Unit cost" className="w-36 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                </div>
              ))}
              <div className="flex items-center justify-between flex-wrap gap-3">
                <button type="button" onClick={() => setPoForm({ ...poForm, lines: [...poForm.lines, { item_name: '', quantity: '', unit_cost: '' }] })} className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:underline">+ Add line</button>
                <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Create PO</button>
              </div>
            </form>
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {pos.map((po) => (
                <li key={po.id} className="py-3">
                  <div className="flex items-center justify-between gap-3 flex-wrap">
                    <div>
                      <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">{po.po_no} <span className="font-normal text-slate-400">· {po.supplier_name} · {money(po.total)}</span></p>
                      <p className="text-xs text-slate-400">{po.order_date}{po.bu_name ? ` · ${po.bu_name}` : ''} · {po.lines.map((l) => `${l.item_name} ×${l.quantity}`).join(', ')}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE[po.status] || STATUS_BADGE.open}`}>{po.status}</span>
                      {po.stage_label && <span className="text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300">{po.stage_label}</span>}
                      {(po.approval_stage === 'first' || po.approval_stage === 'final') && po.status === 'draft' && <>
                        <button onClick={async () => { try { await api.approvePO(po.id); } catch (err) { setError(err.message); } load(); }} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Approve</button>
                        <button onClick={async () => { try { await api.rejectPOApproval(po.id); } catch (err) { setError(err.message); } load(); }} className="text-xs font-semibold text-red-500 hover:underline">Reject</button>
                      </>}
                      {po.status === 'draft' && <button onClick={async () => { await api.orderPO(po.id); load(); }} className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:underline">Order</button>}
                      {(po.status === 'draft' || po.status === 'ordered') && <button onClick={async () => { await api.receivePO(po.id); setNotice(`Received ${po.po_no} — stock updated, AP posted.`); setTimeout(() => setNotice(''), 4000); load(); }} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Receive</button>}
                      {po.status === 'received' && <button onClick={async () => { await api.payPO(po.id); load(); }} className="text-xs font-semibold text-violet-600 dark:text-violet-400 hover:underline">Pay</button>}
                      {(po.status === 'received' || po.status === 'paid') && <button onClick={() => { setPoReturnFor(poReturnFor === po.id ? null : po.id); setPoReturnQty({}); }} className="text-xs font-semibold text-amber-600 dark:text-amber-400 hover:underline">Return items</button>}
                      {(po.status === 'draft' || po.status === 'ordered') && <button onClick={async () => { await api.cancelPO(po.id); load(); }} className="text-xs font-semibold text-red-500 hover:underline">Cancel</button>}
                    </div>
                  </div>
                  {poReturnFor === po.id && (
                    <div className="mt-2 rounded-lg border border-amber-200 dark:border-amber-500/30 bg-amber-50/60 dark:bg-amber-500/5 p-3">
                      <p className="text-xs font-bold text-amber-700 dark:text-amber-300 mb-2">Return items from {po.po_no} — stocks out and posts a debit note</p>
                      {po.lines.map((l) => (
                        <div key={l.id} className="flex items-center gap-2 mb-1.5">
                          <span className="flex-1 text-xs text-slate-600 dark:text-slate-300">{l.item_name} <span className="text-slate-400">(received {l.quantity})</span></span>
                          <input type="number" min="0" value={poReturnQty[l.id] || ''} onChange={(e) => setPoReturnQty((q) => ({ ...q, [l.id]: e.target.value }))} placeholder="Return qty" className="w-28 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-2 py-1.5 text-xs" />
                        </div>
                      ))}
                      <button onClick={() => submitPurchaseReturn(po)} className="mt-1 bg-amber-600 hover:bg-amber-700 text-white text-xs font-semibold px-3 py-1.5 rounded-lg">Post debit note</button>
                    </div>
                  )}
                </li>
              ))}
              {pos.length === 0 && <li className="py-8 text-center text-sm text-slate-400">No purchase orders yet</li>}
            </ul>
            {purchaseReturns.length > 0 && (
              <div className="mt-4 border-t border-slate-100 dark:border-slate-800 pt-4">
                <h3 className="text-sm font-bold text-slate-800 dark:text-slate-200 mb-2">Purchase Returns (Debit Notes)</h3>
                <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                  {purchaseReturns.map((r) => (
                    <li key={r.id} className="py-2 flex items-center justify-between text-sm">
                      <span><span className="font-semibold">{r.return_no}</span> <span className="text-slate-400">· against {r.po_no} · {r.return_date}</span></span>
                      <span className="font-semibold text-amber-600 dark:text-amber-400">−{money(r.total)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        )}

        {tab === 'suppliers' && (
          <section className={`${GLASS} p-6`}>
            <form onSubmit={addSupplier} className="flex flex-wrap gap-3 mb-5">
              <input value={supForm.name} onChange={(e) => setSupForm({ ...supForm, name: e.target.value })} placeholder="Supplier name" className="flex-1 min-w-[200px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm" />
              <input value={supForm.contact_person} onChange={(e) => setSupForm({ ...supForm, contact_person: e.target.value })} placeholder="Contact person" className="w-48 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
              <input value={supForm.phone} onChange={(e) => setSupForm({ ...supForm, phone: e.target.value })} placeholder="Phone" className="w-40 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
              <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Add supplier</button>
            </form>
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {suppliers.map((s) => (
                <li key={s.id} className="py-3 flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">{s.name} <span className="text-[10px] font-bold uppercase tracking-wide text-slate-400 ml-1">{s.scope}</span></p>
                    <p className="text-xs text-slate-400">{[s.contact_person, s.phone, s.email].filter(Boolean).join(' · ') || '—'}</p>
                  </div>
                  <button onClick={async () => { await api.deleteSupplier(s.id); load(); }} className="text-xs font-semibold text-red-500 hover:underline">Remove</button>
                </li>
              ))}
              {suppliers.length === 0 && <li className="py-8 text-center text-sm text-slate-400">No suppliers yet</li>}
            </ul>
          </section>
        )}

        {tab === 'invoices' && (
          <div className="space-y-6">
            {agingData && (
              <section className="grid sm:grid-cols-2 gap-4">
                {[
                  { title: 'Receivables (AR)', data: agingData.ar, color: 'text-sky-600 dark:text-sky-400' },
                  { title: 'Payables (AP)', data: agingData.ap, color: 'text-violet-600 dark:text-violet-400' },
                ].map((card) => (
                  <div key={card.title} className={`${GLASS} p-5`}>
                    <p className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-[0.08em]">{card.title} — outstanding {money(card.data.total)}</p>
                    <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2.5">
                      {Object.entries(card.data.buckets).map(([k, v]) => (
                        <p key={k} className="text-xs text-slate-500 dark:text-slate-400">{k === 'current' ? 'Current' : `${k} days`}: <span className={`font-bold tabular-nums ${v > 0 ? card.color : ''}`}>{money(v)}</span></p>
                      ))}
                    </div>
                  </div>
                ))}
              </section>
            )}
            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">New Invoice</h2>
              <form onSubmit={createInvoice} className="space-y-3 mb-6">
                <div className="flex flex-wrap gap-3">
                  <select value={invCreate.customer_id} onChange={(e) => setInvCreate({ ...invCreate, customer_id: e.target.value })} className="flex-1 min-w-[200px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                    <option value="">Select customer…</option>
                    {salesCustomers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                  </select>
                  <input type="date" value={invCreate.invoice_date} onChange={(e) => setInvCreate({ ...invCreate, invoice_date: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                  <input type="date" value={invCreate.due_date} onChange={(e) => setInvCreate({ ...invCreate, due_date: e.target.value })} title="Due date" className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                  <input type="number" value={invCreate.tax_rate} onChange={(e) => setInvCreate({ ...invCreate, tax_rate: e.target.value })} title="Commercial tax %" className="w-24 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                  <select value={invCreate.currency} onChange={(e) => setInvCreate({ ...invCreate, currency: e.target.value })} title="Currency" className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                    <option value="MMK">MMK</option>
                    <option value="USD">USD</option>
                  </select>
                  {invCreate.currency !== 'MMK' && <input type="number" value={invCreate.exchange_rate} onChange={(e) => setInvCreate({ ...invCreate, exchange_rate: e.target.value })} placeholder="Ks per 1 USD (blank = latest)" title="Exchange rate" className="w-52 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />}
                  <select value={invCreate.project_id} onChange={(e) => setInvCreate({ ...invCreate, project_id: e.target.value })} title="Link to project (optional)" className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                    <option value="">Project…</option>
                    {projects.map((pr) => <option key={pr.id} value={pr.id}>{pr.name}</option>)}
                  </select>
                </div>
                {invCreate.lines.map((l, idx) => (
                  <div key={idx} className="flex flex-wrap gap-2">
                    <input value={l.description} onChange={(e) => { const lines = [...invCreate.lines]; lines[idx].description = e.target.value; setInvCreate({ ...invCreate, lines }); }} placeholder="Description" className="flex-1 min-w-[180px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm" />
                    <select value={l.inventory_item_id} onChange={(e) => { const lines = [...invCreate.lines]; lines[idx].inventory_item_id = e.target.value; setInvCreate({ ...invCreate, lines }); }} title="Stock item (optional — for brand/principal reports)" className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-2 py-2.5 text-sm max-w-[180px]">
                      <option value="">Item…</option>
                      {inventory.map((it) => <option key={it.id} value={it.id}>{it.name}</option>)}
                    </select>
                    <input type="number" value={l.quantity} onChange={(e) => { const lines = [...invCreate.lines]; lines[idx].quantity = e.target.value; setInvCreate({ ...invCreate, lines }); }} placeholder="Qty" className="w-24 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                    <input type="number" value={l.unit_price} onChange={(e) => { const lines = [...invCreate.lines]; lines[idx].unit_price = e.target.value; setInvCreate({ ...invCreate, lines }); }} placeholder="Unit price" className="w-36 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                  </div>
                ))}
                <div className="flex items-center justify-between flex-wrap gap-3">
                  <button type="button" onClick={() => setInvCreate({ ...invCreate, lines: [...invCreate.lines, { description: '', quantity: 1, unit_price: '', inventory_item_id: '' }] })} className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:underline">+ Add line</button>
                  <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Create invoice</button>
                </div>
              </form>
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {invoices.map((inv) => (
                  <li key={inv.id} className="py-3">
                    <div className="flex items-center justify-between gap-3 flex-wrap">
                      <div>
                        <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">{inv.invoice_no} <span className="font-normal text-slate-400">· {inv.customer_name} · {money(inv.total)}</span>{inv.currency && inv.currency !== 'MMK' && <span className="ml-1.5 text-[10px] font-bold px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700 dark:bg-indigo-500/15 dark:text-indigo-300">{inv.currency} @{Number(inv.exchange_rate).toLocaleString()}</span>}</p>
                        <p className="text-xs text-slate-400">{inv.invoice_date}{inv.due_date ? ` · due ${inv.due_date}` : ''} · paid {money(inv.paid)} · balance {money(inv.balance)}</p>
                      </div>
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE[inv.status] || STATUS_BADGE.draft}`}>{inv.status}</span>
                        {inv.stored_status === 'draft' && <button onClick={async () => { await api.sendInvoice(inv.id); load(); }} className="text-xs font-semibold text-sky-600 dark:text-sky-400 hover:underline">Send</button>}
                        {inv.stored_status === 'draft' && <button onClick={async () => { await api.cancelInvoice(inv.id); load(); }} className="text-xs font-semibold text-red-500 hover:underline">Cancel</button>}
                        <a href={api.invoicePdfUrl(inv.id)} className="text-xs font-semibold text-red-500 hover:underline">PDF</a>
                        <a href={api.deliveryNoteUrl(inv.id)} className="text-xs font-semibold text-slate-500 dark:text-slate-400 hover:underline">Delivery Note</a>
                        {(inv.stored_status === 'sent' || inv.stored_status === 'partial' || inv.stored_status === 'paid') && <button onClick={() => { setReturnFor(returnFor === inv.id ? null : inv.id); setReturnQty({}); }} className="text-xs font-semibold text-amber-600 dark:text-amber-400 hover:underline">Return items</button>}
                        {(inv.stored_status === 'sent' || inv.stored_status === 'partial') && <>
                          <input type="number" value={payAmt[inv.id] || ''} onChange={(e) => setPayAmt((p) => ({ ...p, [inv.id]: e.target.value }))} placeholder="Amount" className="w-28 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-2 py-1.5 text-xs" />
                          <button onClick={async () => { const amt = Number(payAmt[inv.id]); if (!amt) return; try { await api.recordInvoicePayment(inv.id, { amount: amt, payment_date: new Date().toISOString().slice(0, 10), method: 'cash' }); setPayAmt((p) => ({ ...p, [inv.id]: '' })); } catch (err) { setError(err.message); } load(); }} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Record payment</button>
                        </>}
                      </div>
                    </div>
                    {inv.returns > 0 && <p className="text-xs text-amber-600 dark:text-amber-400 mt-1">Credit notes (returns): −{money(inv.returns)}</p>}
                    {returnFor === inv.id && (
                      <div className="mt-2 rounded-lg border border-amber-200 dark:border-amber-500/30 bg-amber-50/60 dark:bg-amber-500/5 p-3">
                        <p className="text-xs font-bold text-amber-700 dark:text-amber-300 mb-2">Return items from {inv.invoice_no} — restocks and posts a credit note</p>
                        {inv.lines.map((l) => (
                          <div key={l.id} className="flex items-center gap-2 mb-1.5">
                            <span className="flex-1 text-xs text-slate-600 dark:text-slate-300">{l.description} <span className="text-slate-400">(invoiced {l.quantity})</span></span>
                            <input type="number" min="0" value={returnQty[l.id] || ''} onChange={(e) => setReturnQty((q) => ({ ...q, [l.id]: e.target.value }))} placeholder="Return qty" className="w-28 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-2 py-1.5 text-xs" />
                          </div>
                        ))}
                        <button onClick={() => submitSalesReturn(inv)} className="mt-1 bg-amber-600 hover:bg-amber-700 text-white text-xs font-semibold px-3 py-1.5 rounded-lg">Post credit note</button>
                      </div>
                    )}
                  </li>
                ))}
                {invoices.length === 0 && <li className="py-8 text-center text-sm text-slate-400">No invoices yet</li>}
              </ul>
            </section>
            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">Customers</h2>
              <form onSubmit={addSalesCustomer} className="flex flex-wrap gap-3 mb-4">
                <input value={custForm.name} onChange={(e) => setCustForm({ ...custForm, name: e.target.value })} placeholder="Customer name" className="flex-1 min-w-[200px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm" />
                <input value={custForm.phone} onChange={(e) => setCustForm({ ...custForm, phone: e.target.value })} placeholder="Phone" className="w-44 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Add customer</button>
              </form>
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {salesCustomers.map((c) => (
                  <li key={c.id} className="py-2.5 flex items-center justify-between">
                    <p className="text-sm font-medium text-slate-800 dark:text-slate-200">{c.name} <span className="text-xs text-slate-400 font-normal">{c.phone || ''}</span></p>
                    <span className="flex items-center gap-3">
                      <span className="text-xs"><span className="text-slate-400 mr-1">Statement:</span><a href={api.opsStatementUrl(c.id, 'pdf')} className="font-semibold text-red-500 hover:underline">PDF</a> <a href={api.opsStatementUrl(c.id, 'xlsx')} className="font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Excel</a></span>
                      <button onClick={async () => { await api.deleteSalesCustomer(c.id); load(); }} className="text-xs font-semibold text-red-500 hover:underline">Remove</button>
                    </span>
                  </li>
                ))}
                {salesCustomers.length === 0 && <li className="py-6 text-center text-sm text-slate-400">No customers yet</li>}
              </ul>
            </section>
            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">Quotations</h2>
              <form onSubmit={createQuotation} className="space-y-3 mb-6">
                <div className="flex flex-wrap gap-3">
                  <select value={quoteForm.customer_id} onChange={(e) => setQuoteForm({ ...quoteForm, customer_id: e.target.value })} className="flex-1 min-w-[200px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                    <option value="">Select customer…</option>
                    {salesCustomers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                  </select>
                  <input type="date" value={quoteForm.quote_date} onChange={(e) => setQuoteForm({ ...quoteForm, quote_date: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                  <input type="date" value={quoteForm.valid_until} onChange={(e) => setQuoteForm({ ...quoteForm, valid_until: e.target.value })} title="Valid until" className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                  <input type="number" value={quoteForm.tax_rate} onChange={(e) => setQuoteForm({ ...quoteForm, tax_rate: e.target.value })} title="Commercial tax %" className="w-24 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                </div>
                {quoteForm.lines.map((l, idx) => (
                  <div key={idx} className="flex flex-wrap gap-2">
                    <input value={l.description} onChange={(e) => { const lines = [...quoteForm.lines]; lines[idx].description = e.target.value; setQuoteForm({ ...quoteForm, lines }); }} placeholder="Description" className="flex-1 min-w-[180px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm" />
                    <input type="number" value={l.quantity} onChange={(e) => { const lines = [...quoteForm.lines]; lines[idx].quantity = e.target.value; setQuoteForm({ ...quoteForm, lines }); }} placeholder="Qty" className="w-24 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                    <input type="number" value={l.unit_price} onChange={(e) => { const lines = [...quoteForm.lines]; lines[idx].unit_price = e.target.value; setQuoteForm({ ...quoteForm, lines }); }} placeholder="Unit price" className="w-36 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                  </div>
                ))}
                <div className="flex items-center justify-between flex-wrap gap-3">
                  <button type="button" onClick={() => setQuoteForm({ ...quoteForm, lines: [...quoteForm.lines, { description: '', quantity: 1, unit_price: '', inventory_item_id: '' }] })} className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:underline">+ Add line</button>
                  <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Create quotation</button>
                </div>
              </form>
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {quotations.map((q) => (
                  <li key={q.id} className="py-3 flex items-center justify-between gap-3 flex-wrap">
                    <div>
                      <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">{q.quote_no} <span className="font-normal text-slate-400">· {q.customer_name} · {money(q.total)}</span></p>
                      <p className="text-xs text-slate-400">{q.quote_date}{q.valid_until ? ` · valid until ${q.valid_until}` : ''}{q.converted_invoice_id ? ` · invoice #${q.converted_invoice_id}` : ''}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE[q.status] || STATUS_BADGE.draft}`}>{q.status}</span>
                      {q.status === 'draft' && <button onClick={async () => { await api.setQuotationStatus(q.id, 'sent'); load(); }} className="text-xs font-semibold text-sky-600 dark:text-sky-400 hover:underline">Send</button>}
                      {q.status === 'sent' && <button onClick={async () => { await api.setQuotationStatus(q.id, 'accepted'); load(); }} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Accept</button>}
                      {q.status === 'sent' && <button onClick={async () => { await api.setQuotationStatus(q.id, 'rejected'); load(); }} className="text-xs font-semibold text-red-500 hover:underline">Reject</button>}
                      {(q.status === 'draft' || q.status === 'sent' || q.status === 'accepted') && <button onClick={async () => { try { const inv = await api.convertQuotation(q.id); setNotice(`${q.quote_no} converted to draft invoice ${inv.invoice_no}.`); setTimeout(() => setNotice(''), 4000); } catch (err) { setError(err.message); } load(); }} className="text-xs font-semibold text-violet-600 dark:text-violet-400 hover:underline">Convert to invoice</button>}
                    </div>
                  </li>
                ))}
                {quotations.length === 0 && <li className="py-6 text-center text-sm text-slate-400">No quotations yet</li>}
              </ul>
            </section>
            {salesReturns.length > 0 && (
              <section className={`${GLASS} p-6`}>
                <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-3">Sales Returns (Credit Notes)</h2>
                <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                  {salesReturns.map((r) => (
                    <li key={r.id} className="py-2.5 flex items-center justify-between gap-3 flex-wrap">
                      <p className="text-sm"><span className="font-semibold">{r.return_no}</span> <span className="text-slate-400">· against {r.invoice_no} · {r.return_date}</span></p>
                      <span className="text-sm font-semibold text-amber-600 dark:text-amber-400">−{money(r.total)}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {principalReport && principalReport.items.length > 0 && (
              <section className={`${GLASS} p-6`}>
                <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-1.5">Sales by Principal / Brand</h2>
                <p className="text-sm text-slate-500 dark:text-slate-400 mb-4">From invoice lines linked to stock items. Total: {money(principalReport.total_revenue)}</p>
                <table className="w-full text-sm min-w-[480px]">
                  <thead className="text-left text-xs uppercase tracking-wider text-slate-400"><tr><th className="py-2 pr-3">Principal</th><th className="py-2 pr-3">Brand</th><th className="py-2 pr-3 text-right">Qty</th><th className="py-2 text-right">Revenue (MMK)</th></tr></thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {principalReport.items.map((g, ix) => (
                      <tr key={ix}><td className="py-2 pr-3 font-medium">{g.principal}</td><td className="py-2 pr-3">{g.brand}</td><td className="py-2 pr-3 text-right tabular-nums">{g.quantity}</td><td className="py-2 text-right tabular-nums font-semibold">{money(g.revenue)}</td></tr>
                    ))}
                  </tbody>
                </table>
              </section>
            )}
          </div>
        )}

        {tab === 'pos' && (
          <div className="space-y-6">
            <div className="rounded-xl border border-sky-300/60 bg-sky-50 dark:bg-sky-500/10 px-4 py-3">
              <p className="text-sm text-sky-700 dark:text-sky-300">POS sales post their own revenue + COGS journals and move stock immediately. <strong>Do not re-enter POS sales in the CEO daily report</strong> — that would double-count the revenue.</p>
            </div>
            {posSummary && (
              <section className="grid grid-cols-2 md:grid-cols-4 gap-4">
                {[
                  { label: "Today's sales", value: posSummary.sale_count },
                  { label: 'Total collected', value: money(posSummary.total) },
                  { label: 'COGS', value: money(posSummary.cogs) },
                  { label: 'Gross profit', value: money(posSummary.gross_profit) },
                ].map((c) => (
                  <div key={c.label} className={`${GLASS} p-4`}>
                    <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">{c.label}</p>
                    <p className="text-lg font-bold text-slate-900 dark:text-slate-100 mt-1">{c.value}</p>
                  </div>
                ))}
                <div className={`${GLASS} p-4 col-span-2 md:col-span-4`}>
                  <p className="text-xs text-slate-500 dark:text-slate-400">By payment method: {Object.entries(posSummary.by_payment_method || {}).map(([m, v]) => `${m}: ${v.count} sale(s), ${money(v.total)}`).join('  ·  ') || 'no sales yet today'}</p>
                </div>
              </section>
            )}
            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">New Sale {!buId && <span className="text-xs font-normal text-amber-500">— select a specific BU first</span>}</h2>
              <div className="flex flex-wrap gap-2 mb-3">
                <select id="pos-item-picker" className="flex-1 min-w-[220px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                  <option value="">Select item…</option>
                  {posItems.map((i) => <option key={i.id} value={i.id}>{i.name} — {i.current_stock} in stock{i.unit_cost ? ` · cost ${money(i.unit_cost)}` : ''}</option>)}
                </select>
                <input id="pos-qty" type="number" defaultValue={1} min="1" className="w-24 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" placeholder="Qty" />
                <input id="pos-price" type="number" className="w-36 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" placeholder="Unit price" />
                <input id="pos-serials" className="w-52 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" placeholder="Serials (comma separated, optional)" />
                <button onClick={() => {
                  const sel = document.getElementById('pos-item-picker');
                  const item = posItems.find((i) => i.id === Number(sel.value));
                  const qty = Number(document.getElementById('pos-qty').value) || 0;
                  const price = Number(document.getElementById('pos-price').value) || 0;
                  const serials = document.getElementById('pos-serials').value.split(',').map((s) => s.trim()).filter(Boolean);
                  if (!item || qty <= 0 || price < 0) { setError('Pick an item, quantity and price'); return; }
                  setPosCart((c) => [...c, { item_id: item.id, name: item.name, quantity: qty, unit_price: price, serial_nos: serials }]);
                  document.getElementById('pos-serials').value = '';
                }} className="bg-slate-700 hover:bg-slate-800 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Add to sale</button>
              </div>
              {posCart.length > 0 && (
                <div className="border-t border-slate-100 dark:border-slate-800 pt-3">
                  <ul className="divide-y divide-slate-100 dark:divide-slate-800 mb-3">
                    {posCart.map((l, idx) => (
                      <li key={idx} className="py-2 flex items-center justify-between text-sm">
                        <span className="font-medium">{l.name} <span className="text-slate-400 font-normal">× {l.quantity} @ {money(l.unit_price)}{l.serial_nos.length ? ` · serials: ${l.serial_nos.join(', ')}` : ''}</span></span>
                        <span className="flex items-center gap-3"><span className="font-semibold tabular-nums">{money(l.quantity * l.unit_price)}</span><button onClick={() => setPosCart((c) => c.filter((_, i) => i !== idx))} className="text-xs font-semibold text-red-500 hover:underline">Remove</button></span>
                      </li>
                    ))}
                  </ul>
                  <div className="flex flex-wrap items-center gap-3">
                    <select value={posPay.payment_method} onChange={(e) => setPosPay({ ...posPay, payment_method: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                      <option value="cash">Cash</option>
                      <option value="bank">Bank</option>
                    </select>
                    <input type="number" value={posPay.tax_rate} onChange={(e) => setPosPay({ ...posPay, tax_rate: e.target.value })} title="Commercial tax %" placeholder="Tax %" className="w-24 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                    <input value={posPay.customer_name} onChange={(e) => setPosPay({ ...posPay, customer_name: e.target.value })} placeholder="Walk-in customer name (optional)" className="flex-1 min-w-[180px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                    <span className="text-sm font-bold">Total: {money(posCart.reduce((s, l) => s + l.quantity * l.unit_price, 0) * (1 + (Number(posPay.tax_rate) || 0) / 100))}</span>
                    <button onClick={completePosSale} className="bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-semibold px-5 py-2.5 rounded-lg transition-colors">Complete Sale</button>
                  </div>
                </div>
              )}
            </section>
            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-3">Recent Sales</h2>
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {posSales.map((s) => (
                  <li key={s.id} className="py-2.5 flex items-center justify-between gap-3 flex-wrap">
                    <div>
                      <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">#{s.id} · {money(s.total)} <span className="font-normal text-slate-400">· {s.payment_method}{s.customer_name ? ` · ${s.customer_name}` : ''}</span></p>
                      <p className="text-xs text-slate-400">{s.sale_date} · {s.lines.map((l) => `${l.description} ×${l.quantity}`).join(', ')}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE[s.status] || STATUS_BADGE.open}`}>{s.status}</span>
                      {s.status === 'completed' && <button onClick={async () => { try { await api.voidPosSale(s.id); setNotice(`POS sale #${s.id} voided — stock restored, journals reversed.`); setTimeout(() => setNotice(''), 4000); } catch (err) { setError(err.message); } load(); }} className="text-xs font-semibold text-red-500 hover:underline">Void</button>}
                    </div>
                  </li>
                ))}
                {posSales.length === 0 && <li className="py-6 text-center text-sm text-slate-400">No POS sales yet</li>}
              </ul>
            </section>
          </div>
        )}

        {tab === 'transfers' && (
          <div className="space-y-6">
            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-1.5">New Stock Transfer</h2>
              <p className="text-sm text-slate-500 dark:text-slate-400 mb-4">Source is the BU selected in the page filter. Sending posts Dr Intercompany Receivable / Cr Inventory in the source books; receiving posts the mirror in the destination books.</p>
              <form onSubmit={createTransfer} className="space-y-3">
                <select value={transferForm.to_business_unit_id} onChange={(e) => setTransferForm({ ...transferForm, to_business_unit_id: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                  <option value="">Destination BU…</option>
                  {bus.filter((b) => String(b.bu_id) !== String(buId)).map((b) => <option key={b.bu_id} value={b.bu_id}>{b.bu_name}</option>)}
                </select>
                {transferForm.lines.map((l, idx) => (
                  <div key={idx} className="flex flex-wrap gap-2">
                    <select value={l.inventory_item_id} onChange={(e) => { const lines = [...transferForm.lines]; lines[idx].inventory_item_id = e.target.value; setTransferForm({ ...transferForm, lines }); }} className="flex-1 min-w-[220px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                      <option value="">Select item (source BU)…</option>
                      {inventory.map((i) => <option key={i.id} value={i.id}>{i.name} — {i.current_stock} in stock</option>)}
                    </select>
                    <input type="number" value={l.quantity} onChange={(e) => { const lines = [...transferForm.lines]; lines[idx].quantity = e.target.value; setTransferForm({ ...transferForm, lines }); }} placeholder="Qty" className="w-28 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                  </div>
                ))}
                <div className="flex items-center justify-between flex-wrap gap-3">
                  <button type="button" onClick={() => setTransferForm({ ...transferForm, lines: [...transferForm.lines, { inventory_item_id: '', quantity: '' }] })} className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:underline">+ Add line</button>
                  <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Create transfer (draft)</button>
                </div>
              </form>
            </section>
            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-3">Transfers</h2>
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {transfers.map((t) => (
                  <li key={t.id} className="py-3 flex items-center justify-between gap-3 flex-wrap">
                    <div>
                      <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">{t.transfer_no} <span className="font-normal text-slate-400">· {t.from_bu_name} → {t.to_bu_name} · {money(t.total_cost)}</span></p>
                      <p className="text-xs text-slate-400">{t.lines.map((l) => `${l.item_name} ×${l.quantity}`).join(', ')}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE[t.status] || STATUS_BADGE.open}`}>{t.status.replace('_', ' ')}</span>
                      {t.status === 'draft' && <button onClick={async () => { try { await api.modSendTransfer(t.id); } catch (err) { setError(err.message); } load(); }} className="text-xs font-semibold text-sky-600 dark:text-sky-400 hover:underline">Send</button>}
                      {t.status === 'in_transit' && <button onClick={async () => { try { await api.modReceiveTransfer(t.id); } catch (err) { setError(err.message); } load(); }} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Receive</button>}
                      {(t.status === 'draft' || t.status === 'in_transit') && <button onClick={async () => { try { await api.modCancelTransfer(t.id); } catch (err) { setError(err.message); } load(); }} className="text-xs font-semibold text-red-500 hover:underline">Cancel</button>}
                    </div>
                  </li>
                ))}
                {transfers.length === 0 && <li className="py-6 text-center text-sm text-slate-400">No stock transfers yet</li>}
              </ul>
            </section>
          </div>
        )}

        {tab === 'hr' && (
          <div className="space-y-6">
            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">Employees</h2>
              <form onSubmit={addEmployee} className="flex flex-wrap gap-3 mb-4">
                <input value={empForm.name} onChange={(e) => setEmpForm({ ...empForm, name: e.target.value })} placeholder="Employee name" className="flex-1 min-w-[180px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm" />
                <input value={empForm.position} onChange={(e) => setEmpForm({ ...empForm, position: e.target.value })} placeholder="Position" className="w-44 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <input type="number" value={empForm.base_salary} onChange={(e) => setEmpForm({ ...empForm, base_salary: e.target.value })} placeholder="Monthly salary" className="w-40 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Add employee</button>
              </form>
              <div className="overflow-x-auto">
                <table className="w-full text-sm min-w-[480px]">
                  <thead className="text-left text-xs uppercase tracking-wider text-slate-400 dark:text-slate-500">
                    <tr><th className="py-2 pr-3">Name</th><th className="py-2 pr-3">Position</th><th className="py-2 pr-3 text-right">Monthly Salary</th><th className="py-2" /></tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {employees.map((e) => (
                      <tr key={e.id}>
                        <td className="py-2 pr-3 font-medium">{e.name}</td>
                        <td className="py-2 pr-3 text-slate-500">{e.position || '—'}</td>
                        <td className="py-2 pr-3 text-right tabular-nums">{money(e.base_salary)}</td>
                        <td className="py-2 text-right"><button onClick={async () => { await api.deleteEmployee(e.id); load(); }} className="text-xs font-semibold text-red-500 hover:underline">Remove</button></td>
                      </tr>
                    ))}
                    {employees.length === 0 && <tr><td colSpan={4} className="py-6 text-center text-slate-400">No employees yet for this BU</td></tr>}
                  </tbody>
                </table>
              </div>
            </section>

            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">Leave Requests</h2>
              <form onSubmit={submitLeave} className="flex flex-wrap gap-3 mb-4">
                <select value={leaveForm.employee_id} onChange={(e) => setLeaveForm({ ...leaveForm, employee_id: e.target.value })} className="flex-1 min-w-[180px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                  <option value="">Select employee…</option>
                  {employees.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
                </select>
                <select value={leaveForm.leave_type} onChange={(e) => setLeaveForm({ ...leaveForm, leave_type: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                  {['annual', 'sick', 'casual', 'unpaid'].map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
                <input type="date" value={leaveForm.start_date} onChange={(e) => setLeaveForm({ ...leaveForm, start_date: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <input type="date" value={leaveForm.end_date} onChange={(e) => setLeaveForm({ ...leaveForm, end_date: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Request leave</button>
              </form>
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {leaveList.map((lv) => (
                  <li key={lv.id} className="py-2.5 flex items-center justify-between gap-3 flex-wrap">
                    <p className="text-sm"><span className="font-semibold">{lv.employee_name}</span> <span className="text-slate-400">· {lv.leave_type} · {lv.start_date} → {lv.end_date} ({lv.days}d)</span></p>
                    <div className="flex items-center gap-2">
                      <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE[lv.status] || STATUS_BADGE.pending}`}>{lv.status}</span>
                      {lv.status === 'pending' && <>
                        <button onClick={async () => { await api.reviewLeave(lv.id, { decision: 'approved' }); load(); }} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Approve</button>
                        <button onClick={async () => { await api.reviewLeave(lv.id, { decision: 'rejected' }); load(); }} className="text-xs font-semibold text-red-500 hover:underline">Reject</button>
                      </>}
                    </div>
                  </li>
                ))}
                {leaveList.length === 0 && <li className="py-6 text-center text-sm text-slate-400">No leave requests</li>}
              </ul>
            </section>

            <section className={`${GLASS} p-6`}>
              <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
                <h2 className="font-bold text-slate-900 dark:text-slate-100">Payroll</h2>
                <div className="flex items-center gap-2">
                  <input type="month" value={payrollPeriod} onChange={(e) => setPayrollPeriod(e.target.value)} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-sm font-semibold" />
                  <button onClick={generatePayroll} className="whitespace-nowrap bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2 rounded-lg transition-colors">Generate run</button>
                </div>
              </div>
              {payrollRuns.map((run) => (
                <div key={run.id} className="border border-slate-200 dark:border-slate-700 rounded-xl p-4 mb-4">
                  <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
                    <p className="text-sm font-bold text-slate-900 dark:text-slate-100">{run.period} <span className="font-normal text-slate-400">· gross {money(run.total_gross)} · deductions {money(run.total_deductions)} · net {money(run.total_net)}</span></p>
                    <div className="flex items-center gap-2">
                      <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE[run.status] || STATUS_BADGE.draft}`}>{run.status}</span>
                      {run.status === 'draft' && <button onClick={async () => { try { await api.approvePayroll(run.id); } catch (err) { setError(err.message); } load(); }} className="text-xs font-semibold text-sky-600 dark:text-sky-400 hover:underline">Approve</button>}
                      {run.status === 'approved' && <button onClick={async () => { try { await api.payPayroll(run.id); } catch (err) { setError(err.message); } setNotice(`Payroll ${run.period} paid — journal posted.`); setTimeout(() => setNotice(''), 4000); load(); }} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Pay (post journal)</button>}
                    </div>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm min-w-[560px]">
                      <thead className="text-left text-xs uppercase tracking-wider text-slate-400 dark:text-slate-500">
                        <tr><th className="py-1.5 pr-3">Employee</th><th className="py-1.5 pr-3 text-right">Base</th><th className="py-1.5 pr-3 text-right">Allowances</th><th className="py-1.5 pr-3 text-right">Deductions</th><th className="py-1.5 text-right">Net</th></tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                        {run.lines.map((l) => (
                          <tr key={l.id}>
                            <td className="py-1.5 pr-3 font-medium">{l.employee_name}</td>
                            <td className="py-1.5 pr-3 text-right tabular-nums">{money(l.base_salary)}</td>
                            <td className="py-1.5 pr-3 text-right">
                              {run.status === 'draft' ? (
                                <input type="number" defaultValue={l.allowances || ''} placeholder="0" onBlur={async (e) => { const v = Number(e.target.value) || 0; if (v !== l.allowances) { await api.updatePayrollLine(run.id, l.id, { allowances: v }); load(); } }} className="w-24 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-2 py-1 text-xs text-right" />
                              ) : <span className="tabular-nums">{money(l.allowances)}</span>}
                            </td>
                            <td className="py-1.5 pr-3 text-right">
                              {run.status === 'draft' ? (
                                <input type="number" defaultValue={(l.manual_deductions ?? l.deductions) || ''} placeholder="0" onBlur={async (e) => { const v = Number(e.target.value) || 0; if (v !== (l.manual_deductions ?? l.deductions)) { await api.updatePayrollLine(run.id, l.id, { deductions: v }); load(); } }} className="w-24 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-2 py-1 text-xs text-right" />
                              ) : <span className="tabular-nums">{money(l.deductions)}{l.advance_deduction > 0 && <span className="block text-[10px] font-normal text-slate-400">incl. advance repayment {money(l.advance_deduction)}</span>}</span>}
                            </td>
                            <td className="py-1.5 text-right tabular-nums font-semibold">{money(l.net)}</td>
                            <td className="py-1.5 pl-3 text-right whitespace-nowrap"><a href={api.opsPayslipUrl(run.id, l.id)} className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:underline">Payslip</a></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
              {payrollRuns.length === 0 && <p className="text-sm text-slate-400 text-center py-4">No payroll runs yet — generate one for this month</p>}
            </section>

            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-1.5">Staff Advances & Loans</h2>
              <p className="text-sm text-slate-500 dark:text-slate-400 mb-4">Approval disburses cash (Dr Staff Advances 1240 / Cr Cash). Payroll automatically deducts the monthly amount and credits the advance back until it settles.</p>
              <form onSubmit={submitAdvance} className="flex flex-wrap gap-3 mb-4">
                <select value={advForm.employee_id} onChange={(e) => setAdvForm({ ...advForm, employee_id: e.target.value })} className="flex-1 min-w-[180px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                  <option value="">Select employee…</option>
                  {employees.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
                </select>
                <input type="number" value={advForm.amount} onChange={(e) => setAdvForm({ ...advForm, amount: e.target.value })} placeholder="Amount (Ks)" className="w-40 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <input type="number" value={advForm.monthly_deduction} onChange={(e) => setAdvForm({ ...advForm, monthly_deduction: e.target.value })} placeholder="Monthly deduction" className="w-44 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <input value={advForm.reason} onChange={(e) => setAdvForm({ ...advForm, reason: e.target.value })} placeholder="Reason (optional)" className="flex-1 min-w-[160px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Request advance</button>
              </form>
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {advances.map((a) => (
                  <li key={a.id} className="py-2.5 flex items-center justify-between gap-3 flex-wrap">
                    <p className="text-sm"><span className="font-semibold">{a.employee_name}</span> <span className="text-slate-400">· {money(a.amount)} · remaining {money(a.remaining_amount)} · {money(a.monthly_deduction)}/mo{a.reason ? ` · ${a.reason}` : ''}</span></p>
                    <div className="flex items-center gap-2">
                      <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE[a.status] || STATUS_BADGE.pending}`}>{a.status}</span>
                      {a.status === 'pending' && <>
                        <button onClick={async () => { try { await api.approveAdvance(a.id); setNotice(`Advance approved for ${a.employee_name} — cash disbursed.`); setTimeout(() => setNotice(''), 4000); } catch (err) { setError(err.message); } load(); }} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Approve & disburse</button>
                        <button onClick={async () => { await api.rejectAdvance(a.id); load(); }} className="text-xs font-semibold text-red-500 hover:underline">Reject</button>
                      </>}
                    </div>
                  </li>
                ))}
                {advances.length === 0 && <li className="py-6 text-center text-sm text-slate-400">No staff advances</li>}
              </ul>
            </section>

            <section className={`${GLASS} p-6`}>
              <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
                <h2 className="font-bold text-slate-900 dark:text-slate-100">Attendance</h2>
                <input type="month" value={attMonth} onChange={(e) => setAttMonth(e.target.value)} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-sm font-semibold" />
              </div>
              <p className="text-sm text-slate-500 dark:text-slate-400 mb-3">Set today's status per employee, then save. The table below summarises {attMonth}.</p>
              <div className="overflow-x-auto mb-4">
                <table className="w-full text-sm min-w-[560px]">
                  <thead className="text-left text-xs uppercase tracking-wider text-slate-400 dark:text-slate-500">
                    <tr><th className="py-2 pr-3">Employee</th><th className="py-2 pr-3">Today</th><th className="py-2 pr-3 text-right">Present</th><th className="py-2 pr-3 text-right">Absent</th><th className="py-2 pr-3 text-right">Late</th><th className="py-2 pr-3 text-right">Leave</th><th className="py-2 text-right">Half day</th></tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {employees.map((e) => {
                      const sum = attSummary.find((s) => s.employee_id === e.id) || {};
                      const todayRec = attRecords.find((r) => r.employee_id === e.id && r.date === new Date().toISOString().slice(0, 10));
                      return (
                        <tr key={e.id}>
                          <td className="py-2 pr-3 font-medium">{e.name}</td>
                          <td className="py-2 pr-3">
                            <select value={attEdits[e.id] ?? todayRec?.status ?? ''} onChange={(ev) => setAttEdits((m) => ({ ...m, [e.id]: ev.target.value }))} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-2 py-1.5 text-xs">
                              <option value="">—</option>
                              {['present', 'absent', 'late', 'leave', 'half_day'].map((st) => <option key={st} value={st}>{st.replace('_', ' ')}</option>)}
                            </select>
                          </td>
                          <td className="py-2 pr-3 text-right tabular-nums">{sum.present || 0}</td>
                          <td className="py-2 pr-3 text-right tabular-nums">{sum.absent || 0}</td>
                          <td className="py-2 pr-3 text-right tabular-nums">{sum.late || 0}</td>
                          <td className="py-2 pr-3 text-right tabular-nums">{sum.leave || 0}</td>
                          <td className="py-2 text-right tabular-nums">{sum.half_day || 0}</td>
                        </tr>
                      );
                    })}
                    {employees.length === 0 && <tr><td colSpan={7} className="py-6 text-center text-slate-400">No employees yet for this BU</td></tr>}
                  </tbody>
                </table>
              </div>
              <button onClick={saveAttendance} className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Save today's attendance</button>
            </section>

            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">Leave Balances ({new Date().getFullYear()})</h2>
              <div className="overflow-x-auto">
                <table className="w-full text-sm min-w-[560px]">
                  <thead className="text-left text-xs uppercase tracking-wider text-slate-400 dark:text-slate-500">
                    <tr><th className="py-2 pr-3">Employee</th><th className="py-2 pr-3 text-right">Annual (earned) — used / left</th><th className="py-2 pr-3 text-right">Casual — used / left</th><th className="py-2 text-right">Sick (medical) — used / left</th></tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                    {leaveBals.map((row) => (
                      <tr key={row.employee_id}>
                        <td className="py-2 pr-3 font-medium">{row.employee_name}</td>
                        <td className="py-2 pr-3 text-right tabular-nums">{row.balances.annual.used} / {row.balances.annual.remaining}</td>
                        <td className="py-2 pr-3 text-right tabular-nums">{row.balances.casual.used} / {row.balances.casual.remaining}</td>
                        <td className="py-2 text-right tabular-nums">{row.balances.sick.used} / {row.balances.sick.remaining}</td>
                      </tr>
                    ))}
                    {leaveBals.length === 0 && <tr><td colSpan={4} className="py-6 text-center text-slate-400">No employees yet for this BU</td></tr>}
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        )}

        {tab === 'expenses' && (
          <section className={`${GLASS} p-6`}>
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {expenses.map((e) => (
                <li key={e.id} className="py-3 flex items-center justify-between gap-3 flex-wrap">
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">{e.title} <span className="text-slate-400 font-normal">· {money(e.amount)}</span></p>
                    <p className="text-xs text-slate-400 dark:text-slate-500">{e.bu_name} · by {e.submitted_by_name}{e.category ? ` · ${e.category}` : ''}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE[e.status]}`}>{e.status}</span>
                    {e.status === 'pending' && e.stage_label && <span className="text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300">{e.stage_label}</span>}
                    {e.receipt_url
                      ? <a href={e.receipt_url} target="_blank" rel="noreferrer" className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:underline">Receipt</a>
                      : <label className="text-xs font-semibold text-slate-400 hover:text-brand-600 cursor-pointer hover:underline">+ Receipt<input type="file" accept="image/*,.pdf" className="hidden" onChange={async (ev) => { const f = ev.target.files?.[0]; if (f) { try { await api.opsUploadReceipt(e.id, f); load(); } catch (err) { setError(err.message); } } }} /></label>}
                    {e.status === 'pending' && <>
                      <button onClick={() => review(e, 'approved')} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Approve</button>
                      <button onClick={() => review(e, 'rejected')} className="text-xs font-semibold text-red-500 hover:underline">Reject</button>
                    </>}
                  </div>
                </li>
              ))}
              {expenses.length === 0 && <li className="py-8 text-center text-sm text-slate-400">No expense requests</li>}
            </ul>
          </section>
        )}

        {tab === 'documents' && (
          <section className={`${GLASS} p-6`}>
            <form onSubmit={addDocument} className="flex flex-wrap gap-3 mb-5">
              <input value={docForm.title} onChange={(e) => setDocForm({ ...docForm, title: e.target.value })} placeholder="Document title (select a BU first)" className="flex-1 min-w-[200px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500" />
              <select value={docForm.document_type} onChange={(e) => setDocForm({ ...docForm, document_type: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                <option value="license">License</option>
                <option value="contract">Contract</option>
                <option value="permit">Permit</option>
                <option value="other">Other</option>
              </select>
              <input type="date" value={docForm.expiry_date} onChange={(e) => setDocForm({ ...docForm, expiry_date: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
              <input value={docForm.notes} onChange={(e) => setDocForm({ ...docForm, notes: e.target.value })} placeholder="Notes (optional)" className="flex-1 min-w-[160px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3.5 py-2.5 text-sm" />
              <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Add</button>
            </form>
            <div className="flex justify-end mb-3">
              <button onClick={checkExpiry} className="text-xs font-semibold bg-red-500/10 border border-red-500/20 text-red-600 dark:text-red-400 px-3.5 py-2 rounded-lg hover:bg-red-500/20 transition-colors">
                Send expiry alerts
              </button>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm min-w-[560px]">
                <thead className="text-left text-xs uppercase tracking-wider text-slate-400 dark:text-slate-500">
                  <tr><th className="py-2 pr-3">Document</th><th className="py-2 pr-3">Type</th><th className="py-2 pr-3">BU</th><th className="py-2 pr-3 text-right">Expires</th><th className="py-2 text-right">Status</th></tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {documents.map((d) => (
                    <tr key={d.id}>
                      <td className="py-2.5 pr-3 font-semibold text-slate-800 dark:text-slate-200">{d.title}
                        {d.notes && <span className="block text-xs font-normal text-slate-400">{d.notes}</span>}
                      </td>
                      <td className="py-2.5 pr-3 text-slate-500 dark:text-slate-400 capitalize">{d.document_type}</td>
                      <td className="py-2.5 pr-3 text-slate-500 dark:text-slate-400">{d.bu_name}</td>
                      <td className="py-2.5 pr-3 text-right tabular-nums">{d.expiry_date}</td>
                      <td className="py-2.5 text-right">
                        {d.days_left !== null && d.days_left < 0 && <span className="text-[10px] font-bold bg-red-500 text-white rounded-full px-2 py-0.5">EXPIRED</span>}
                        {d.days_left !== null && d.days_left >= 0 && d.days_left <= 30 && <span className="text-[10px] font-bold bg-amber-500/10 border border-amber-500/20 text-amber-600 dark:text-amber-400 rounded-full px-2 py-0.5">{d.days_left}d left</span>}
                        {(d.days_left === null || d.days_left > 30) && <span className="text-[10px] font-bold bg-emerald-500/10 border border-emerald-500/20 text-emerald-600 dark:text-emerald-400 rounded-full px-2 py-0.5">OK</span>}
                        <button onClick={async () => { await api.deleteDocument(d.id); load(); }} className="ml-2 text-xs font-semibold text-slate-400 hover:text-red-500">Remove</button>
                      </td>
                    </tr>
                  ))}
                  {documents.length === 0 && <tr><td colSpan={5} className="py-8 text-center text-slate-400">No documents yet</td></tr>}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {tab === 'staff' && (
          <section className={`${GLASS} p-6 overflow-x-auto`}>
            <table className="w-full text-sm min-w-[560px]">
              <thead className="text-left text-xs uppercase tracking-wider text-slate-400 dark:text-slate-500">
                <tr><th className="py-2 pr-3">Date</th><th className="py-2 pr-3">BU</th><th className="py-2 pr-3 text-right">Total</th><th className="py-2 pr-3 text-right">Present</th><th className="py-2 pr-3 text-right">Absent</th><th className="py-2 pr-3 text-right">Hires</th><th className="py-2 text-right">Left</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {staff.map((s) => (
                  <tr key={s.id}>
                    <td className="py-2.5 pr-3 tabular-nums">{s.report_date}</td>
                    <td className="py-2.5 pr-3">{buName(s.business_unit_id)}</td>
                    <td className="py-2.5 pr-3 text-right tabular-nums">{s.total_staff}</td>
                    <td className="py-2.5 pr-3 text-right tabular-nums text-emerald-600 dark:text-emerald-400">{s.present}</td>
                    <td className="py-2.5 pr-3 text-right tabular-nums text-red-500">{s.absent}</td>
                    <td className="py-2.5 pr-3 text-right tabular-nums">{s.new_hires}</td>
                    <td className="py-2.5 text-right tabular-nums">{s.resignations}</td>
                  </tr>
                ))}
                {staff.length === 0 && <tr><td colSpan={7} className="py-8 text-center text-slate-400">No staff records in the last 14 days</td></tr>}
              </tbody>
            </table>
          </section>
        )}

        {tab === 'customers' && (
          <section className={`${GLASS} p-6 overflow-x-auto`}>
            <table className="w-full text-sm min-w-[560px]">
              <thead className="text-left text-xs uppercase tracking-wider text-slate-400 dark:text-slate-500">
                <tr><th className="py-2 pr-3">Date</th><th className="py-2 pr-3">BU</th><th className="py-2 pr-3 text-right">Footfall</th><th className="py-2 pr-3 text-right">Transactions</th><th className="py-2 pr-3 text-right">Complaints</th><th className="py-2 text-right">Satisfaction</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {customers.map((c) => (
                  <tr key={c.id}>
                    <td className="py-2.5 pr-3 tabular-nums">{c.report_date}</td>
                    <td className="py-2.5 pr-3">{buName(c.business_unit_id)}</td>
                    <td className="py-2.5 pr-3 text-right tabular-nums">{c.footfall ?? '—'}</td>
                    <td className="py-2.5 pr-3 text-right tabular-nums">{c.transactions ?? '—'}</td>
                    <td className="py-2.5 pr-3 text-right tabular-nums">{c.complaints}</td>
                    <td className="py-2.5 text-right tabular-nums">{c.satisfaction_score ? `${c.satisfaction_score} / 5` : '—'}</td>
                  </tr>
                ))}
                {customers.length === 0 && <tr><td colSpan={6} className="py-8 text-center text-slate-400">No customer metrics in the last 14 days</td></tr>}
              </tbody>
            </table>
          </section>
        )}

        {tab === 'forecast' && forecast && (
          <section className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {[
              { label: `MTD Revenue (${forecast.days_elapsed}/${forecast.days_in_month} days)`, value: money(forecast.mtd_revenue) },
              { label: 'Projected Month-End Revenue', value: money(forecast.projected_revenue) },
              { label: `MTD Cash Flow`, value: money(forecast.mtd_cash_flow) },
              { label: 'Projected Month-End Cash Flow', value: money(forecast.projected_cash_flow) },
            ].map((c) => (
              <div key={c.label} className={`${GLASS} p-5`}>
                <p className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-[0.08em]">{c.label}</p>
                <p className="font-bold text-slate-900 dark:text-slate-100 text-[24px] leading-tight mt-1.5 tabular-nums">{c.value}</p>
                <p className="text-[11px] text-slate-400 dark:text-slate-500 mt-1">{forecast.month} · {forecast.reporting_days} reporting days</p>
              </div>
            ))}
          </section>
        )}

        {tab === 'projects' && (
          <div className="space-y-6">
            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">New Project / Job</h2>
              <form onSubmit={async (e) => { e.preventDefault(); if (!buId) { setError('Select a specific business unit first'); return; } try { await api.modCreateProject({ business_unit_id: Number(buId), name: projForm.name, project_type: projForm.project_type, customer_id: projForm.customer_id ? Number(projForm.customer_id) : undefined }); setProjForm({ name: '', project_type: 'general', customer_id: '' }); load(); } catch (err) { setError(err.message); } }} className="flex flex-wrap gap-3">
                <input value={projForm.name} onChange={(e) => setProjForm({ ...projForm, name: e.target.value })} placeholder="Project name" className="flex-1 min-w-[220px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <select value={projForm.project_type} onChange={(e) => setProjForm({ ...projForm, project_type: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                  <option value="general">General</option><option value="contract">Contract</option><option value="content">Content / Media</option><option value="shipment">Logistics job</option><option value="order">Furniture order</option><option value="development">Property development</option>
                </select>
                <select value={projForm.customer_id} onChange={(e) => setProjForm({ ...projForm, customer_id: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                  <option value="">Customer…</option>
                  {salesCustomers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
                <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Create project</button>
              </form>
            </section>
            <section className={`${GLASS} p-6`}>
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {projects.map((pr) => (
                  <li key={pr.id} className="py-3">
                    <div className="flex items-center justify-between gap-3 flex-wrap">
                      <div>
                        <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">{pr.name} <span className="font-normal text-slate-400">· {pr.project_type}{pr.customer_name ? ` · ${pr.customer_name}` : ''} · {pr.bu_name}</span></p>
                        {projPL[pr.id] && <p className="text-xs text-slate-500 mt-0.5">Revenue {money(projPL[pr.id].revenue)} · Costs {money(projPL[pr.id].total_costs)} (PO {money(projPL[pr.id].po_costs)} + expenses {money(projPL[pr.id].expense_costs)}) · <strong className={projPL[pr.id].profit >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500'}>Profit {money(projPL[pr.id].profit)}{projPL[pr.id].margin_pct !== null ? ` (${Number(projPL[pr.id].margin_pct).toFixed(0)}%)` : ''}</strong></p>}
                      </div>
                      <div className="flex items-center gap-2">
                        <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE[pr.status] || 'text-slate-500 border-slate-200'}`}>{pr.status}</span>
                        <button onClick={async () => { try { const pl = await api.modProjectPL(pr.id); setProjPL((m) => ({ ...m, [pr.id]: pl })); } catch (err) { setError(err.message); } }} className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:underline">P&L</button>
                        {pr.status === 'active' && <button onClick={async () => { await api.modProjectStatus(pr.id, 'completed'); load(); }} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Complete</button>}
                      </div>
                    </div>
                  </li>
                ))}
                {projects.length === 0 && <li className="py-8 text-center text-sm text-slate-400">No projects yet — link invoices, POs and expenses to a project to see its profit</li>}
              </ul>
            </section>
          </div>
        )}

        {tab === 'shipments' && (
          <div className="space-y-6">
            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">New Shipment</h2>
              <form onSubmit={async (e) => { e.preventDefault(); if (!buId) { setError('Select a specific business unit first'); return; } try { await api.modCreateShipment({ business_unit_id: Number(buId), origin: shipForm.origin, destination: shipForm.destination, vehicle_no: shipForm.vehicle_no || undefined, driver_name: shipForm.driver_name || undefined, shipment_date: shipForm.shipment_date, freight_charge: Number(shipForm.freight_charge) || 0, fuel_cost: Number(shipForm.fuel_cost) || 0, toll_fees: Number(shipForm.toll_fees) || 0, driver_fees: Number(shipForm.driver_fees) || 0, customer_id: shipForm.customer_id ? Number(shipForm.customer_id) : undefined }); setShipForm({ origin: '', destination: '', vehicle_no: '', driver_name: '', shipment_date: new Date().toISOString().slice(0, 10), freight_charge: '', fuel_cost: '', toll_fees: '', driver_fees: '', customer_id: '' }); load(); } catch (err) { setError(err.message); } }} className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <input value={shipForm.origin} onChange={(e) => setShipForm({ ...shipForm, origin: e.target.value })} placeholder="Origin" className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <input value={shipForm.destination} onChange={(e) => setShipForm({ ...shipForm, destination: e.target.value })} placeholder="Destination" className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <input value={shipForm.vehicle_no} onChange={(e) => setShipForm({ ...shipForm, vehicle_no: e.target.value })} placeholder="Vehicle no" className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <input value={shipForm.driver_name} onChange={(e) => setShipForm({ ...shipForm, driver_name: e.target.value })} placeholder="Driver" className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <input type="date" value={shipForm.shipment_date} onChange={(e) => setShipForm({ ...shipForm, shipment_date: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <select value={shipForm.customer_id} onChange={(e) => setShipForm({ ...shipForm, customer_id: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                  <option value="">Customer…</option>
                  {salesCustomers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
                <input type="number" value={shipForm.freight_charge} onChange={(e) => setShipForm({ ...shipForm, freight_charge: e.target.value })} placeholder="Freight charge" className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <input type="number" value={shipForm.fuel_cost} onChange={(e) => setShipForm({ ...shipForm, fuel_cost: e.target.value })} placeholder="Fuel cost" className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <input type="number" value={shipForm.toll_fees} onChange={(e) => setShipForm({ ...shipForm, toll_fees: e.target.value })} placeholder="Toll fees" className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <input type="number" value={shipForm.driver_fees} onChange={(e) => setShipForm({ ...shipForm, driver_fees: e.target.value })} placeholder="Driver fees" className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Add shipment</button>
              </form>
            </section>
            <section className={`${GLASS} p-6`}>
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {shipments.map((sh) => (
                  <li key={sh.id} className="py-3 flex items-center justify-between gap-3 flex-wrap">
                    <div>
                      <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">{sh.origin} → {sh.destination} <span className="font-normal text-slate-400">· {sh.shipment_date}{sh.vehicle_no ? ` · ${sh.vehicle_no}` : ''}{sh.driver_name ? ` · ${sh.driver_name}` : ''}</span></p>
                      <p className="text-xs text-slate-400">Freight {money(sh.freight_charge)} · Costs {money(sh.total_costs)} · <strong className={sh.profit >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500'}>Profit {money(sh.profit)}</strong></p>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE[sh.status] || 'text-slate-500 border-slate-200'}`}>{sh.status}</span>
                      {sh.status === 'planned' && <button onClick={async () => { await api.modShipmentStatus(sh.id, 'in_transit'); load(); }} className="text-xs font-semibold text-sky-600 dark:text-sky-400 hover:underline">Start trip</button>}
                      {sh.status === 'in_transit' && <button onClick={async () => { await api.modShipmentStatus(sh.id, 'delivered'); load(); }} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Delivered</button>}
                    </div>
                  </li>
                ))}
                {shipments.length === 0 && <li className="py-8 text-center text-sm text-slate-400">No shipments yet</li>}
              </ul>
            </section>
          </div>
        )}

        {tab === 'production' && (
          <div className="space-y-6">
            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">Bill of Materials (recipe per finished item)</h2>
              <form onSubmit={async (e) => { e.preventDefault(); if (!buId) { setError('Select a specific business unit first'); return; } try { await api.modCreateBom({ business_unit_id: Number(buId), finished_item_id: Number(bomForm.finished_item_id), name: bomForm.name || undefined, components: bomForm.components.filter((c) => c.component_item_id).map((c) => ({ component_item_id: Number(c.component_item_id), quantity_per_unit: Number(c.quantity_per_unit) || 0 })) }); setBomForm({ finished_item_id: '', name: '', components: [{ component_item_id: '', quantity_per_unit: '' }] }); load(); } catch (err) { setError(err.message); } }} className="space-y-3">
                <div className="flex flex-wrap gap-3">
                  <select value={bomForm.finished_item_id} onChange={(e) => setBomForm({ ...bomForm, finished_item_id: e.target.value })} className="flex-1 min-w-[200px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                    <option value="">Finished item…</option>
                    {inventory.map((it) => <option key={it.id} value={it.id}>{it.name}</option>)}
                  </select>
                  <input value={bomForm.name} onChange={(e) => setBomForm({ ...bomForm, name: e.target.value })} placeholder="BOM name (optional)" className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                </div>
                {bomForm.components.map((c, ix) => (
                  <div key={ix} className="flex flex-wrap gap-2">
                    <select value={c.component_item_id} onChange={(e) => { const comps = [...bomForm.components]; comps[ix].component_item_id = e.target.value; setBomForm({ ...bomForm, components: comps }); }} className="flex-1 min-w-[200px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                      <option value="">Component item…</option>
                      {inventory.map((it) => <option key={it.id} value={it.id}>{it.name}</option>)}
                    </select>
                    <input type="number" value={c.quantity_per_unit} onChange={(e) => { const comps = [...bomForm.components]; comps[ix].quantity_per_unit = e.target.value; setBomForm({ ...bomForm, components: comps }); }} placeholder="Qty per unit" className="w-36 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                  </div>
                ))}
                <div className="flex items-center justify-between flex-wrap gap-3">
                  <button type="button" onClick={() => setBomForm({ ...bomForm, components: [...bomForm.components, { component_item_id: '', quantity_per_unit: '' }] })} className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:underline">+ Add component</button>
                  <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Save BOM</button>
                </div>
              </form>
              <ul className="divide-y divide-slate-100 dark:divide-slate-800 mt-4">
                {boms.map((b) => (
                  <li key={b.id} className="py-2.5 text-sm"><span className="font-semibold text-slate-800 dark:text-slate-200">{b.finished_item_name}</span> <span className="text-slate-400">= {b.components.map((c) => `${c.component_name} ×${c.quantity_per_unit}`).join(', ')}</span></li>
                ))}
                {boms.length === 0 && <li className="py-4 text-center text-sm text-slate-400">No BOMs yet</li>}
              </ul>
            </section>
            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">Production Orders</h2>
              <form onSubmit={async (e) => { e.preventDefault(); try { await api.modCreateProduction({ bom_id: Number(prodForm.bom_id), quantity: Number(prodForm.quantity), labour_cost: Number(prodForm.labour_cost) || 0, overhead_cost: Number(prodForm.overhead_cost) || 0 }); setProdForm({ bom_id: '', quantity: '', labour_cost: '', overhead_cost: '' }); load(); } catch (err) { setError(err.message); } }} className="flex flex-wrap gap-3 mb-5">
                <select value={prodForm.bom_id} onChange={(e) => setProdForm({ ...prodForm, bom_id: e.target.value })} className="flex-1 min-w-[200px] rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                  <option value="">Select BOM…</option>
                  {boms.map((b) => <option key={b.id} value={b.id}>{b.finished_item_name}{b.name ? ` — ${b.name}` : ''}</option>)}
                </select>
                <input type="number" value={prodForm.quantity} onChange={(e) => setProdForm({ ...prodForm, quantity: e.target.value })} placeholder="Qty to produce" className="w-36 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <input type="number" value={prodForm.labour_cost} onChange={(e) => setProdForm({ ...prodForm, labour_cost: e.target.value })} placeholder="Labour cost" className="w-36 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <input type="number" value={prodForm.overhead_cost} onChange={(e) => setProdForm({ ...prodForm, overhead_cost: e.target.value })} placeholder="Overhead" className="w-36 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Plan production</button>
              </form>
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {prodOrders.map((o) => (
                  <li key={o.id} className="py-3 flex items-center justify-between gap-3 flex-wrap">
                    <div>
                      <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">{o.finished_item_name} ×{o.quantity} <span className="font-normal text-slate-400">· {o.production_date}</span></p>
                      {o.status === 'completed' && <p className="text-xs text-slate-400">Materials {money(o.materials_cost)} · Labour {money(o.labour_cost)} · Overhead {money(o.overhead_cost)} · <strong>Unit cost {money(o.unit_cost)}</strong></p>}
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE[o.status] || 'text-slate-500 border-slate-200'}`}>{o.status}</span>
                      {o.status === 'planned' && <button onClick={async () => { try { await api.modCompleteProduction(o.id); setNotice('Production completed — finished goods stocked at full cost.'); setTimeout(() => setNotice(''), 4000); } catch (err) { setError(err.message); } load(); }} className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 hover:underline">Complete</button>}
                    </div>
                  </li>
                ))}
                {prodOrders.length === 0 && <li className="py-8 text-center text-sm text-slate-400">No production orders yet</li>}
              </ul>
            </section>
          </div>
        )}

        {tab === 'properties' && (
          <div className="space-y-6">
            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">Property Units</h2>
              <form onSubmit={async (e) => { e.preventDefault(); if (!buId) { setError('Select a specific business unit first'); return; } try { await api.modCreateUnit({ business_unit_id: Number(buId), development: unitForm.development || undefined, unit_no: unitForm.unit_no, unit_type: unitForm.unit_type, size_sqft: unitForm.size_sqft ? Number(unitForm.size_sqft) : undefined, asking_price: unitForm.asking_price ? Number(unitForm.asking_price) : undefined }); setUnitForm({ development: '', unit_no: '', unit_type: 'apartment', size_sqft: '', asking_price: '' }); load(); } catch (err) { setError(err.message); } }} className="flex flex-wrap gap-3 mb-5">
                <input value={unitForm.development} onChange={(e) => setUnitForm({ ...unitForm, development: e.target.value })} placeholder="Development / building" className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <input value={unitForm.unit_no} onChange={(e) => setUnitForm({ ...unitForm, unit_no: e.target.value })} placeholder="Unit no" className="w-32 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <select value={unitForm.unit_type} onChange={(e) => setUnitForm({ ...unitForm, unit_type: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                  <option value="apartment">Apartment</option><option value="condo">Condo</option><option value="house">House</option><option value="land">Land</option><option value="shop">Shop</option><option value="office">Office</option>
                </select>
                <input type="number" value={unitForm.size_sqft} onChange={(e) => setUnitForm({ ...unitForm, size_sqft: e.target.value })} placeholder="Sq ft" className="w-28 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <input type="number" value={unitForm.asking_price} onChange={(e) => setUnitForm({ ...unitForm, asking_price: e.target.value })} placeholder="Asking price" className="w-40 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Add unit</button>
              </form>
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {units.map((u) => (
                  <li key={u.id} className="py-3">
                    <div className="flex items-center justify-between gap-3 flex-wrap">
                      <p className="text-sm font-semibold text-slate-800 dark:text-slate-200">{u.development ? `${u.development} · ` : ''}Unit {u.unit_no} <span className="font-normal text-slate-400">· {u.unit_type}{u.size_sqft ? ` · ${u.size_sqft} sqft` : ''} · {money(u.asking_price)}</span></p>
                      <span className={`text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${STATUS_BADGE[u.status] || 'text-slate-500 border-slate-200'}`}>{u.status}</span>
                    </div>
                    {u.status === 'available' && (
                      <form onSubmit={async (e) => { e.preventDefault(); const f = sellForms[u.id] || {}; try { const r = await api.modSellUnit(u.id, { customer_id: Number(f.customer_id), downpayment_pct: Number(f.down) || 0, installments: Number(f.months) || 0, first_due_date: f.first_due || undefined }); setNotice(`Sold via ${r.invoice_no}${r.installments.length ? ` — ${r.installments.length} installments scheduled` : ''}. Send the invoice to book the sale.`); setTimeout(() => setNotice(''), 5000); load(); } catch (err) { setError(err.message); } }} className="flex flex-wrap gap-2 mt-2">
                        <select value={(sellForms[u.id] || {}).customer_id || ''} onChange={(e) => setSellForms((m) => ({ ...m, [u.id]: { ...(m[u.id] || {}), customer_id: e.target.value } }))} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                          <option value="">Buyer…</option>
                          {salesCustomers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                        </select>
                        <input type="number" value={(sellForms[u.id] || {}).down || ''} onChange={(e) => setSellForms((m) => ({ ...m, [u.id]: { ...(m[u.id] || {}), down: e.target.value } }))} placeholder="Downpayment %" className="w-32 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                        <input type="number" value={(sellForms[u.id] || {}).months || ''} onChange={(e) => setSellForms((m) => ({ ...m, [u.id]: { ...(m[u.id] || {}), months: e.target.value } }))} placeholder="Monthly installments" className="w-40 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                        <input type="date" value={(sellForms[u.id] || {}).first_due || ''} onChange={(e) => setSellForms((m) => ({ ...m, [u.id]: { ...(m[u.id] || {}), first_due: e.target.value } }))} title="First due date" className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                        <button type="submit" className="text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white px-3.5 py-2 rounded-lg transition-colors">Sell unit</button>
                      </form>
                    )}
                  </li>
                ))}
                {units.length === 0 && <li className="py-8 text-center text-sm text-slate-400">No property units yet</li>}
              </ul>
            </section>
            <section className={`${GLASS} p-6`}>
              <h2 className="font-bold text-slate-900 dark:text-slate-100 mb-4">Rental Leases</h2>
              <form onSubmit={async (e) => { e.preventDefault(); if (!buId) { setError('Select a specific business unit first'); return; } try { await api.modCreateLease({ business_unit_id: Number(buId), unit_id: Number(leaseForm.unit_id), tenant_id: Number(leaseForm.tenant_id), monthly_rent: Number(leaseForm.monthly_rent), deposit_amount: Number(leaseForm.deposit_amount) || 0, start_date: leaseForm.start_date }); setLeaseForm({ unit_id: '', tenant_id: '', monthly_rent: '', deposit_amount: '', start_date: new Date().toISOString().slice(0, 10) }); load(); } catch (err) { setError(err.message); } }} className="flex flex-wrap gap-3 mb-5">
                <select value={leaseForm.unit_id} onChange={(e) => setLeaseForm({ ...leaseForm, unit_id: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                  <option value="">Unit…</option>
                  {units.filter((u) => u.status === 'available').map((u) => <option key={u.id} value={u.id}>{u.development ? `${u.development} · ` : ''}{u.unit_no}</option>)}
                </select>
                <select value={leaseForm.tenant_id} onChange={(e) => setLeaseForm({ ...leaseForm, tenant_id: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm">
                  <option value="">Tenant…</option>
                  {salesCustomers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
                <input type="number" value={leaseForm.monthly_rent} onChange={(e) => setLeaseForm({ ...leaseForm, monthly_rent: e.target.value })} placeholder="Monthly rent" className="w-36 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <input type="number" value={leaseForm.deposit_amount} onChange={(e) => setLeaseForm({ ...leaseForm, deposit_amount: e.target.value })} placeholder="Deposit" className="w-32 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <input type="date" value={leaseForm.start_date} onChange={(e) => setLeaseForm({ ...leaseForm, start_date: e.target.value })} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2.5 text-sm" />
                <button type="submit" className="bg-brand-600 hover:bg-brand-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg transition-colors">Start lease</button>
              </form>
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {leases.map((l) => (
                  <li key={l.id} className="py-3 flex items-center justify-between gap-3 flex-wrap">
                    <p className="text-sm"><span className="font-semibold text-slate-800 dark:text-slate-200">{l.development ? `${l.development} · ` : ''}Unit {l.unit_no}</span> <span className="text-slate-400">· {l.tenant_name} · {money(l.monthly_rent)}/month · since {l.start_date}{l.last_billed_period ? ` · billed to ${l.last_billed_period}` : ''}</span></p>
                    {l.status === 'active' && <button onClick={async () => { try { const r = await api.modBillLease(l.id); setNotice(`Rent invoice ${r.invoice_no} created for ${r.period} (draft — send it from Invoices).`); setTimeout(() => setNotice(''), 5000); load(); } catch (err) { setError(err.message); } }} className="text-xs font-semibold text-brand-600 dark:text-brand-400 hover:underline">Bill this month</button>}
                  </li>
                ))}
                {leases.length === 0 && <li className="py-6 text-center text-sm text-slate-400">No leases yet</li>}
              </ul>
            </section>
          </div>
        )}

        <Footer />
      </main>
    </div>
  );
}
