# Demo domain (Task 6 decision)

**Chosen domain: equipment safety inspection at an oil refinery.**

## Why this domain
* It is the natural fit for the architecture (and for the MRPL problem the project started from).
* It needs exactly the features Theme 6 judges look for: confidential engineering records, role-based access,
  scanned paperwork, human sign-off on safety decisions, and a strong "this data must never leave the site" story.
* The story is easy to explain in one sentence: *"An inspector's scanned report shows a vessel wall below its safe minimum;
  the system drafts the approval note, entirely offline, with every step auditable."*

## Fictional setting (nothing here is real)
* Company: **Bharat Coastal Refinery (BCR)**, invented. No MRPL data or documents are used.
* Equipment: pump P-101A, heat exchanger E-205, pressure vessel V-210.
* Key numbers that tie the demo together:
  * V-210 minimum allowable thickness **5.0 mm** (SOP-01); retirement thickness **4.2 mm** (SOP-06)
  * TP3 reads **4.8 mm** (IR-2026-013) and **4.7 mm** in the smudged scan (IR-2026-014)
  * Corrosion rate from the history file: **0.40 mm/year**; remaining life from 4.8 mm: **1.5 years**
  * SOP-06: remaining life under 2 years means **Engineering Manager approval required**

## People in the demo (`demo_data/users.json`)
| User | Clearance | Role in the story |
|---|---|---|
| Asha (u-junior) | internal | Junior engineer: must NOT see confidential or board-only material |
| Vikram (u-senior) | confidential | Senior engineer / approver: signs off the note |
| Neha (u-comply) | confidential | Compliance officer: consent and erasure |
| Sandeep (u-admin) | internal | Security admin: sovereignty dashboard and ledger |

## What to say about MRPL
"The problem came from a refinery setting. We used a fully synthetic, fictional refinery so no confidential data is involved."
