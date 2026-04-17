---
name: fin-monthly-close-kickoff
description: "Monthly close kickoff — initiates the month-end closing process with a checklist, simplified P&L (Stripe + Omie + Evo Academy), pending reconciliations, receivables, payables, and action items for the finance team. Trigger when user says 'monthly close', 'start closing', 'closing kickoff', or on the 1st of each month."
---

# Monthly Close Kickoff

Monthly routine that initiates the closing process: generates a checklist, simplified income statement, pending items, and action items for the finance team.

**Always respond in English.**

**IMPORTANT:** This routine runs on the 1st of each month and refers to the PREVIOUS month's close.

## Step 1 — Determine period

- Reference month: the month prior to the current one (e.g., if today is April 1st, close March)
- Period: first to last day of the reference month

## Step 2 — Collect month's data (silently)

### 2a. Revenue (Stripe)
Use `/int-stripe`:
- Total succeeded charges for the month
- MRR at end of month vs start
- Subscriptions: new, canceled, upgrades, downgrades
- Month's refunds

### 2b. Revenue and expenses (Omie)
Use `/int-omie`:
- Total revenue received during the month
- Total expenses paid during the month
- Categorize by type (Personnel, Infrastructure, Services, Marketing, Taxes, etc.)
- Invoices issued during the month
- Invoices that should have been issued but were not


### 2c. Revenue (Evo Academy)
Call `GET /api/v1/analytics/summary?period=30d` (env: `$EVO_ACADEMY_BASE_URL`, auth: `Bearer $EVO_ACADEMY_API_KEY`):
- `revenue.total` → receita bruta do mês
- `orders.completed / pending / refunded` → contagem por status
- `subscriptions.active / cancelled` → base e churn do mês

Fetch todos os orders do mês: `GET /api/v1/analytics/orders?status=completed&created_after=YYYY-MM-01&created_before=YYYY-MM-31&per_page=100`
- Itere por cursor até `has_more=false`
- Some `amount` → receita total do mês
- Separe por produto: Evo Academy (R$950/mês), Evolution Builder (R$970/mês), Curso Agentic Engineer (R$2k/mês), Beta Access (R$370/mês), one-time (Blueprint Pack, Fast Start Pro), Evo Setup (R$5/mês)
- Identifique renovações (`is_renewal=true`) vs novos clientes

Fetch assinaturas ativas no fim do mês: `GET /api/v1/analytics/subscriptions?status=active&per_page=100`
- MRR Evo Academy = soma de `plan.price` das ativas

### 2d. Outstanding receivables
- List all open receivables (from the month or earlier)
- Highlight overdue items

### 2e. Next month's payables
- List payables due in the current month (the upcoming month)

### 2f. Previous month (for comparison)
- Read the previous month's financial report from `workspace/finance/reports/monthly/` if it exists
- Or use data from the last monthly close

## Step 3 — Build simplified income statement

Structure the income statement with:

| Account | Actual | Prior Month | Variance |
|---------|--------|-------------|----------|
| Gross Revenue (Stripe) | | | |
| Gross Revenue (Evo Academy) | | | |
| Gross Revenue (Omie/Services) | | | |
| (-) Taxes | | | |
| **Net Revenue** | | | |
| (-) Personnel | | | |
| (-) Infrastructure | | | |
| (-) Third-party Services | | | |
| (-) Marketing | | | |
| (-) Other | | | |
| **Total Expenses** | | | |
| **Operating Result** | | | |
| Margin | | | |

## Step 4 — Build closing checklist

Generate a checklist with initial status for each item:

1. **Reconcile Stripe** — verify all charges match received payments
2. **Reconcile Evo Academy** — verify orders and subscriptions match expected MRR
3. **Reconcile Omie** — verify entries and exits in the ERP are correct
4. **Issue pending invoices** — list invoices that need to be issued (finance team)
5. **Collect overdue accounts** — list clients with late payments
6. **Categorize expenses** — verify all expenses are categorized
7. **Review entries** — verify manual or atypical entries
8. **Calculate taxes** — verify month's tax obligations
9. **Generate final income statement** — after reconciliations, generate the definitive P&L
10. **Approve close** — the responsible person reviews and approves

Possible statuses:
- `done` (checkmark) — already completed automatically
- `pending` (circle) — needs to be done
- `blocked` (x) — depends on something external
- `na` (dash) — not applicable this month

## Step 5 — Identify pending items for the finance team

List in clear bullets what the finance team needs to handle:
- Invoices to issue (which clients, amounts)
- Payments to confirm
- Missing documents
- Deadlines

## Step 6 — Closing observations

Relevant notes:
- Atypical (non-recurring) expenses
- Client plan changes
- Any anomalies identified
- Impact of monthly events (e.g., corporate event, new partnership)

## Step 7 — Classify close status

Badge:
- **green** "On track": most items ok, no blockers
- **yellow** "In progress": pending items but no risk
- **red** "Delayed": blockers or critical pending items

## Step 8 — Generate HTML

Read the template at `.claude/templates/html/custom/monthly-close.html` and replace ALL `{{PLACEHOLDER}}`.

For checklist:
```html
<div class="checklist-item">
  <div class="check-icon done/pending/blocked/na">checkmark/circle/x/dash</div>
  <div class="checklist-text">
    <div class="cl-title">Item name</div>
    <div class="cl-detail">Detail or observation</div>
  </div>
  <div class="checklist-owner">Finance / Admin / Auto</div>
</div>
```

For income statement:
```html
<tr>
  <td>Account name</td>
  <td class="right">R$ X,XXX.XX</td>
  <td class="right">R$ X,XXX.XX</td>
  <td class="right var-positive/var-negative">+X% / -X%</td>
</tr>
```

Total rows use `class="total"`:
```html
<tr class="total">
  <td>Operating Result</td>
  <td class="right">R$ X,XXX.XX</td>
  <td class="right">R$ X,XXX.XX</td>
  <td class="right var-positive">+X%</td>
</tr>
```

Values in Brazilian format: R$ 1.234,56

## Step 9 — Save

Save to:
```
workspace/finance/reports/monthly/[C] YYYY-MM-monthly-close.html
```

Create the directory `workspace/finance/reports/monthly/` if it does not exist.

## Step 10 — Confirm

```
## Monthly Close Kickoff generated

**File:** workspace/finance/reports/monthly/[C] YYYY-MM-monthly-close.html
**Month:** {reference month}
**Revenue:** R$ X,XXX | **Expenses:** R$ X,XXX | **Result:** R$ X,XXX
**Checklist:** X/10 completed
**Finance team pending items:** {N} items
```

### Notify via Telegram

Upon completion, send a short summary via Telegram to the user:
- Use the Telegram MCP: `reply(chat_id="YOUR_CHAT_ID", text="...")`
- Format: emoji + "Monthly Close" + month's result + pending items (2-3 lines)
