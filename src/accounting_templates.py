"""Standard Chart of Accounts templates (commonly used SME structure).

One group code standard: the same code means the same account in every BU,
which is what makes group consolidation (Phase 2) a simple sum by code.
Each BU's CoA is cloned from the template matching its business nature;
accountants may add BU-specific sub-accounts under the standard groups.

Account tuple: (code, name, account_type, parent_code, is_header)
account_type: asset / liability / equity / income / expense
"""

BASE_ACCOUNTS = [
    # --- Assets ---
    ("1000", "Assets", "asset", None, True),
    ("1100", "Current Assets", "asset", "1000", True),
    ("1110", "Cash on Hand", "asset", "1100", False),
    ("1120", "Cash at Bank", "asset", "1100", False),
    ("1130", "Accounts Receivable", "asset", "1100", False),
    ("1140", "Inventory", "asset", "1100", False),
    ("1150", "Prepaid Expenses", "asset", "1100", False),
    ("1160", "Other Current Assets", "asset", "1100", False),
    ("1200", "Fixed Assets", "asset", "1000", True),
    ("1210", "Equipment & Machinery", "asset", "1200", False),
    ("1220", "Furniture & Fixtures", "asset", "1200", False),
    ("1230", "Vehicles", "asset", "1200", False),
    ("1290", "Accumulated Depreciation", "asset", "1200", False),
    # --- Liabilities ---
    ("2000", "Liabilities", "liability", None, True),
    ("2100", "Current Liabilities", "liability", "2000", True),
    ("2110", "Accounts Payable", "liability", "2100", False),
    ("2120", "Accrued Expenses", "liability", "2100", False),
    ("2130", "Tax Payable", "liability", "2100", False),
    ("2140", "Short-term Loans", "liability", "2100", False),
    ("2200", "Long-term Loans", "liability", "2000", False),
    # --- Equity ---
    ("3000", "Equity", "equity", None, True),
    ("3100", "Owner Capital", "equity", "3000", False),
    ("3200", "Retained Earnings", "equity", "3000", False),
    ("3300", "Owner Drawings", "equity", "3000", False),
    # --- Income ---
    ("4000", "Income", "income", None, True),
    ("4100", "Sales Revenue", "income", "4000", False),
    ("4200", "Service Income", "income", "4000", False),
    ("4900", "Other Income", "income", "4000", False),
    # --- Cost of sales ---
    ("5000", "Cost of Sales", "expense", None, True),
    ("5100", "Purchases / Cost of Goods Sold", "expense", "5000", False),
    # --- Operating expenses ---
    ("6000", "Operating Expenses", "expense", None, True),
    ("6100", "Rent Expense", "expense", "6000", False),
    ("6200", "Salaries & Wages", "expense", "6000", False),
    ("6300", "Utilities", "expense", "6000", False),
    ("6400", "Transport & Fuel", "expense", "6000", False),
    ("6500", "Marketing & Advertising", "expense", "6000", False),
    ("6600", "Office & Admin Expenses", "expense", "6000", False),
    ("6700", "Repairs & Maintenance", "expense", "6000", False),
    ("6800", "Communication & Internet", "expense", "6000", False),
    ("6900", "Depreciation Expense", "expense", "6000", False),
    # --- Other ---
    ("7000", "Other Expenses & Income", "expense", None, True),
    ("7100", "Interest Expense", "expense", "7000", False),
    ("7200", "Tax Expense", "expense", "7000", False),
    ("7900", "Other Expenses", "expense", "7000", False),
]

# Extra accounts per business nature (appended to BASE).
TYPE_EXTRAS = {
    "retail": [
        ("5110", "Store Supplies Consumed", "expense", "5000", False),
        ("6510", "POS & Store Operating Costs", "expense", "6000", False),
    ],
    "distribution": [
        ("5120", "Warehouse Handling Costs", "expense", "5000", False),
        ("5130", "Delivery Costs (COGS)", "expense", "5000", False),
        ("1170", "Goods in Transit", "asset", "1100", False),
    ],
    "logistics": [
        ("6410", "Vehicle Fuel", "expense", "6000", False),
        ("6420", "Vehicle Maintenance", "expense", "6000", False),
        ("6430", "Driver Wages & Allowances", "expense", "6000", False),
        ("4210", "Freight & Delivery Income", "income", "4000", False),
    ],
    "services": [
        ("4220", "Project / Contract Income", "income", "4000", False),
        ("6610", "Software & Subscriptions", "expense", "6000", False),
    ],
    "head_office": [
        ("6620", "Group Management Costs", "expense", "6000", False),
        ("2180", "Intercompany Payable", "liability", "2100", False),
        ("1180", "Intercompany Receivable", "asset", "1100", False),
    ],
}

TEMPLATE_TYPES = ["retail", "distribution", "logistics", "services", "head_office"]


def template_accounts(template_type: str) -> list[tuple]:
    """Full account list for a template type (base + type extras)."""
    return BASE_ACCOUNTS + TYPE_EXTRAS.get(template_type, [])


# Default auto-posting rules (group level; BU rows can override).
# source_field -> (debit_code, credit_code)
DEFAULT_POSTING_RULES = [
    ("daily_financial.revenue", "1110", "4100"),
    ("daily_financial.expenses", "6600", "1110"),
    ("expense_request", "6600", "1110"),
]

# Expense category -> expense account code (falls back to the posting rule).
EXPENSE_CATEGORY_ACCOUNTS = {
    "transport": "6400", "fuel": "6400",
    "salary": "6200", "salaries": "6200", "wages": "6200",
    "rent": "6100",
    "utilities": "6300", "electricity": "6300", "water": "6300",
    "marketing": "6500", "advertising": "6500",
    "maintenance": "6700", "repair": "6700", "repairs": "6700",
    "supplies": "6600", "office": "6600", "admin": "6600",
    "communication": "6800", "internet": "6800", "phone": "6800",
}
