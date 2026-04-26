---
name: fin-daily-pulse
description: "Daily financial pulse — queries Stripe (MRR, charges, churn, failures), Omie (accounts payable/receivable, invoices) and Evo Academy (courses, subscriptions, Summit tickets) to generate an HTML snapshot of the company's financial health. Trigger when user says 'financial pulse', 'financial snapshot', or 'financial metrics'."
---

# Financial Pulse — Daily Financial Snapshot

Daily routine that pulls data from Stripe, Omie and Evo Academy to generate an HTML snapshot of financial health.

**Always respond in English.**

## Currency Conversion Rule (apply to every Stripe value)

**All monetary values MUST be converted to BRL before summing or displaying as R$.**

1. Fetch the USD-to-BRL exchange rate from `https://api.exchangerate-api.com/v4/latest/USD` (field `rates.BRL`).
   - If the API call fails, use fallback rate **5.75**.
   - Log the rate used in the report (e.g., "USD/BRL: 5.7832" or "USD/BRL: 5.75 (fallback)").
2. For each Stripe item, read the `currency` field (lowercase ISO-4217, e.g. `"usd"`, `"brl"`, `"idr"`).
   - `brl`: amount is in centavos — divide by 100, use as-is.
   - `usd`: divide by 100, then multiply by the USD-to-BRL rate.
   - Any other currency (e.g. `idr`, `mxn`, `eur`): **exclude from BRL totals**. Append a warning: `WARNING: Skipped {currency} {amount/100} (customer {id}) — unsupported currency, manual review required.`
3. **Never add raw amounts of different currencies together.** Convert first, then sum.

## Step 1 — Collect Stripe data (silently)

Use the `/int-stripe` skill to fetch:

### 1a. MRR and Subscriptions
- List active subscriptions (`status=active`).
- For each subscription, read `currency` and `plan.amount` (centavos).
- Apply the Currency Conversion Rule above to convert each amount to BRL.
- Sum all converted amounts — Stripe MRR in BRL.
- Log currency breakdown (e.g., "195 subs: 116 BRL, 78 USD, 1 excluded IDR").
- Compare with previous data if available in `workspace/finance/`.

### 1b. Today's Charges
- List charges created today (`created` >= start of day UTC-3).
- For each charge, read `currency` and `amount` (centavos).
- Apply the Currency Conversion Rule to convert each charge to BRL.
- Sum converted succeeded amounts — today's revenue in BRL.
- Count charges with `status=succeeded` vs `status=failed`.
- Flag any single converted charge exceeding R$ 10,000 as potentially anomalous.

### 1c. Churn (last 30 days)
- Fetch Stripe events with `type=customer.subscription.deleted`, paginating ALL pages until `has_more=false` — never stop at the first page.
- Count unique canceled subscription IDs — churn count.
- Churn rate = canceled / (total active + canceled) * 100.
- Use this event-based method consistently every run.

### 1d. Refunds (last 7 days)
- List refunds from the last 7 days.
- Apply the Currency Conversion Rule to each refund before summing.
- Report total refunded in BRL.

### 1e. New customers (last 7 days)
- List customers created in the last 7 days.

## Step 2 — Collect Omie data (silently)

Use the `/int-omie` skill to fetch:

### 2a. Overdue receivables
- Fetch receivables with due date before today and status "open"

### 2b. Payables (next 7 days)
- Fetch payables with due date in the next 7 days

### 2c. Invoices
- Fetch invoices pending issuance
- Count invoices issued in the current month


## Step 2.5 — Collect Evo Academy data (silently)

Call the Evo Academy Analytics API directly:
- **Base URL:** `$EVO_ACADEMY_BASE_URL` (env var)
- **Auth:** `Authorization: Bearer $EVO_ACADEMY_API_KEY`

### 2.5a. Summary do dia
```
GET /api/v1/analytics/summary?period=today
```
Captura: `revenue.total`, `orders.completed`, `orders.pending`, `orders.failed`, `subscriptions.active`, `students.new_in_period`

### 2.5b. Orders completados hoje
```
GET /api/v1/analytics/orders?status=completed&created_after=YYYY-MM-DD&per_page=100
```
(today in BRT; convert to UTC: `created_after = date.today().isoformat()`)
- Paginate until `meta.has_more = false`
- Sum `amount` of all orders — Evo Academy daily revenue
- Split by type: renewals (`is_renewal=true`) vs new (`is_renewal=false`)
- Group by product: courses, subscriptions, tickets, others

### 2.5c. MRR de assinaturas ativas (Evo Academy)
```
GET /api/v1/analytics/subscriptions?status=active&per_page=100
```
- Paginate until `meta.has_more = false`
- Sum `plan.price` of each active subscription — Evo Academy MRR

## Step 3 — Day's transactions

Consolidate all financial transactions for the day:
- Stripe charges (revenue, already converted to BRL per the Currency Conversion Rule)
- Evo Academy orders (revenue — courses / subscriptions / tickets)
- Payments recorded in Omie (expenses)
- Refunds (converted to BRL)

Format each transaction with: type (Revenue/Expense/Refund), description, amount in BRL, status.

**Total revenue = Stripe today (BRL) + Evo Academy today**
**Total MRR = Stripe MRR (BRL) + Evo Academy MRR**

## Step 4 — Classify financial health

Define the health badge (CSS class):
- **green** "Healthy": MRR stable or growing, no significant delinquency, churn < 5%
- **yellow** "Warning": churn between 5-10%, or overdue accounts > R$ 1,000, or payment failures > 3
- **red** "Risk": churn > 10%, or overdue accounts > R$ 5,000, or MRR declining

## Step 5 — Alerts

Generate list of financial alerts:
- Payment failures that need retry or follow-up
- Accounts overdue for more than 7 days
- Invoices that should have been issued
- Churn above normal levels
- Any anomalies in amounts
- Currencies excluded from totals (currency conversion warnings from Step 1)

If there are no alerts: "No financial alerts at this time."

## Step 6 — Generate HTML

Read the template at `.claude/templates/html/custom/financial-pulse.html` and replace ALL `{{PLACEHOLDER}}` with the collected data.

For transactions (dynamic table):
```html
<tr>
  <td><span class="badge green/red/yellow">Revenue/Expense/Refund</span></td>
  <td>Description</td>
  <td class="right">R$ X,XXX.XX</td>
  <td><span class="badge green/yellow">Confirmed/Pending</span></td>
</tr>
```

Values in Brazilian format: R$ 1.234,56

## Step 7 — Save

Save the filled HTML to:
```
workspace/finance/reports/daily/[C] YYYY-MM-DD-financial-pulse.html
```

Create the directory `workspace/finance/reports/daily/` if it does not exist.

## Step 8 — Confirm

Output the completion summary in the terminal:

```
## Financial Pulse generated

**File:** workspace/finance/reports/daily/[C] YYYY-MM-DD-financial-pulse.html
**MRR total:** R$ X,XXX (Stripe: R$ X,XXX | Evo Academy: R$ X,XXX)
**Receita hoje:** R$ X,XXX | **Subscriptions:** N | **Churn:** X%
**USD/BRL rate:** X.XXXX (live) or 5.75 (fallback)
**Alerts:** {N} attention points
```

Do NOT send a Telegram message here — the caller handles notifications.
