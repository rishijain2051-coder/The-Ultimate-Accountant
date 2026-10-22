# Permissions (detailed)

Each person on the People tab gets any set of these. Admin stays one permission.

| Permission | Key | Gives |
|---|---|---|
| **Console** | | |
| Reconciliation | `con.recon` | GST 2B/Tally, bank and party-statement reconciliation; runs, items, decisions, suppliers, workspaces |
| Exports register | `con.exports` | The export documents register: add, correct, settle, mark, read with AI |
| Reports and downloads | `con.reports` | Download Excel/PDF/CSV reports, follow-up packs, kept files and zips |
| Console settings | `con.settings` | Change the console's settings (tolerance, financial year, letter details, update checks) |
| AI checker | `con.ai` | Get or remove the AI runtime, turn the AI second reading on or off |
| **Bridge (bills into Tally)** | | |
| Upload bills | `tib.upload` | Upload bills, use the phone code |
| Review entries | `tib.review` | See, edit, reject, rematch and check entries |
| Post to Tally | `tib.post` | Approve entries into Tally (post or draft), create masters |
| Bank statements | `tib.bank` | Upload bank statements, check and post bank rows |
| Sync from Tally | `tib.sync` | Read companies, ledgers, stock and voucher types from Tally |
| **Books (Keeper)** | | |
| See the books | `books.view` | Open the Keeper's books on approved phones and laptops (gets the data key) |
| Alerts | `books.alerts` | Overdue and big-payment notifications |
| Balances | `books.balances` | Cash and bank, accounts and party balances screens |
| Sales and purchases | `books.sales` | Sales/purchase figures and the transactions list |
| **Admin** | `admin` | People, devices, office server controls, Keeper page, Tally backups, Tally/AI connection settings |

Rules
- The old broad permissions keep working: `console` means every `con.*`, `tib` every `tib.*`, `books` every `books.*`. No database migration; the People tab writes the detailed keys from now on.
- A person needs at least one `con.*` to open the console at all, at least one `tib.*` for the Bridge, `books.view` for the books. Read-only basics (health, bootstrap, the app shell) need any key of that area.
- `books.balances` and `books.sales` only hide screens in the viewer: every approved books device holds the one data key, so they are not a cryptographic boundary. Say so on the People tab.
- One shared list of keys, labels, descriptions and presets. Presets tick a set at once and can then be adjusted:
  - **Owner**: everything including admin.
  - **Accountant**: all `con.*` + all `tib.*` + `books.view`, `books.alerts`, `books.balances`, `books.sales`.
  - **Data entry**: `tib.upload`, `tib.review`.
  - **Viewer**: `books.view`, `books.balances`.
- Where enforced: office server gate (`app/office/gate.py` need() and route mapping), Bridge routers, Keeper; control plane (`cp/auth.py` has(), `cp/admin.py` cap validation, `cp/blobs.py` books links need `books.view`, `cp/push.py` alerts need `books.alerts`); office server key release (`app/office/keys.py`: `books.view`); viewer screens (`control-plane/public/books.js`).
