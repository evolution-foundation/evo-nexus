---
name: fin-weekly-report
description: "Weekly financial report — consolidates Stripe, Omie and Evo Academy data for the week: revenue (courses, subscriptions, tickets), expenses, cash flow projection, overdue accounts, and variance analysis. Trigger when user says 'financial weekly', 'weekly financial report', or 'financial summary of the week'."
---

# Financial Weekly — Weekly Financial Report

Weekly routine that consolidates the week's financial data: revenue, expenses, Stripe, Omie, Evo Academy, projected cash flow, and analysis.

**Always respond in English.**

## Step 1 — Collect the week's revenue (silently)

### 1a. Stripe — revenue
Use `/int-stripe` to fetch:
- Succeeded charges for the week (Mon-Sun) → group by type/plan
- Compare with previous week
- Current MRR vs start of week
- New customers vs cancellations
- Payment failures

### 1b. Omie — revenue
Use `/int-omie` to fetch:
- Confirmed receipts for the week
- Invoices issued during the week


### 1c. Evo Academy — revenue
Call `GET /api/v1/analytics/summary?period=7d` (env: `$EVO_ACADEMY_BASE_URL`, auth: `Bearer $EVO_ACADEMY_API_KEY`):
- `revenue.total` → receita bruta da semana
- `orders.completed` → número de vendas
- `subscriptions.active` / `subscriptions.cancelled` → net change

Fetch orders da semana: `GET /api/v1/analytics/orders?status=completed&created_after=YYYY-MM-DD&per_page=100`
- Itere por cursor até `has_more=false`
- Some `amount` → receita total Evo Academy na semana
- Separe: renovações vs novos, one-time vs assinatura

Fetch assinaturas novas na semana: `GET /api/v1/analytics/subscriptions?status=active&created_after=YYYY-MM-DD&per_page=100`
- MRR adicionado = soma dos `plan.price` de assinaturas criadas na semana

Group revenue by category:
- Stripe Subscriptions
- Evo Academy — Courses & Subscriptions
- Evo Academy — One-time (tickets, packs)
- Services / Consulting
- Partnerships
- Other

## Step 2 — Collect the week's expenses (silently)

### 2a. Omie — expenses
Use `/int-omie` to fetch:
- Payments made during the week
- Categorize: Personnel, Infrastructure, Services, Marketing, Taxes, Other

### 2b. Comparison
- Calculate variance vs previous week for each category
- Calculate % of total for each category

## Step 3 — Detailed Stripe metrics

Consolidate the week's Stripe metrics:
- MRR and variance
- Total active subscriptions and variance
- Churn rate
- New customers
- Payment failures and at-risk amount

## Step 4 — Detailed Omie metrics

Consolidate the week's Omie metrics:
- Overdue receivables (delinquency)
- Next week's payables
- Invoices pending issuance
- Invoices issued during the week
- Confirmed receipts

## Step 4.5 — Detailed Evo Academy metrics

Consolidate Evo Academy's week metrics:
- MRR (sum of all active subscription `plan.price`) and variance vs prior week
- New subscriptions vs cancellations
- One-time revenue (tickets, packs, live events)
- Top-selling products of the week
- Students enrolled (`students.new_in_period`)

## Step 5 — Cash flow projection (4 weeks)

Based on collected data, project:
- Expected inflows (Stripe recurring + Evo Academy subscriptions + receivables)
- Expected outflows (payables + recurring expenses)
- Balance and cumulative by week

## Step 6 — Overdue accounts

List all overdue accounts (receivable and payable):
- Client/Vendor, type, amount, due date, days overdue

## Step 7 — Analysis and recommendations

Write a brief analysis (3-5 bullets) covering:
- Revenue trend (growing/stable/declining)
- Out-of-pattern spending
- Delinquency status
- Cash flow (comfortable or tight)

Write recommended actions (bullets):
- Collections to make
- Invoices to issue
- Payments to expedite/postpone
- Any flags for the responsible person or finance team

## Step 8 — Classify financial health

Health badge (CSS class):
- **green** "Healthy": revenue > expenses, no significant delinquency, positive cash flow
- **yellow** "Warning": tight margins, or delinquency > R$ 2,000, or projected negative cash flow
- **red** "Risk": expenses > revenue, or delinquency > R$ 10,000, or runway < 3 months

## Step 9 — Generate HTML

Read the template at `.claude/templates/html/custom/financial-weekly.html` and replace ALL `{{PLACEHOLDER}}`.

For dynamic revenue/expense tables:
```html
<tr>
  <td>Category Name</td>
  <td class="right">R$ X,XXX.XX</td>
  <td class="right">XX%</td>
  <td class="right var-positive/var-negative">+X% / -X%</td>
</tr>
```

For cash flow:
```html
<tr>
  <td>Week DD/MM - DD/MM</td>
  <td class="right">R$ X,XXX</td>
  <td class="right">R$ X,XXX</td>
  <td class="right" style="color:var(--green/--red)">R$ X,XXX</td>
  <td class="right">R$ XX,XXX</td>
</tr>
```

Values in Brazilian format: R$ 1.234,56

## Step 10 — Save

Save to:
```
workspace/finance/reports/weekly/[C] YYYY-WXX-financial-weekly.html
```

Create the directory `workspace/finance/reports/weekly/` if it does not exist.

## Step 11 — Confirm

```
## Financial Weekly generated

**File:** workspace/finance/reports/weekly/[C] YYYY-WXX-financial-weekly.html
**Revenue:** R$ X,XXX ({var}%) | **Expenses:** R$ X,XXX ({var}%)
**MRR total:** R$ X,XXX (Stripe: R$ X,XXX | Evo Academy: R$ X,XXX) | **Projected 30d balance:** R$ XX,XXX
**Alerts:** {N} overdue accounts | {N} pending invoices
```
