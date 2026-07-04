# Synthetic Financial Submission Examples

These files are fake data for development and classifier testing. They are shaped to resemble the kinds of records a small business might submit to a bank or SBA lender: profit and loss, balance sheet, bank statement activity, accounts receivable aging, accounts payable aging, debt schedule, inventory support, projected cash flow, and use of proceeds.

The `case_label` field is a synthetic review label:

- `normal`: internally consistent financial package with ordinary business activity.
- `needs_review`: package includes weak documentation, cash-flow concerns, unusual transfers, or use-of-proceeds questions.

Do not treat these labels as legal conclusions. They are training signals for audit-risk triage.
