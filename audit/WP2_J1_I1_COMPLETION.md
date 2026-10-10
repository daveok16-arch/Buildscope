# J1 — I1 completion (read-only on the audit DB copy)

DB: `/tmp/h1_readonly.db` (a copy of `data/oppintel.db`). `today = 2026-10-09`.
Instrument: `audit/scripts/h1_d5_reconcile.py` (opens `mode=ro`).

## J1a — the two "22"s are labelled and confirmed different

`[3]` is **public projects in the window** (`permit_date >= 2026-09-09`, both gates). The other
"22" is **all rows dated exactly 2026-09-09** (which are non-public). They are disjoint:

```
-- the two '22's are different sets --
   [3] public in-window (>= 2026-09-09)            = 22 projects
       rows dated exactly 2026-09-09 (non-public) = 22 projects
       intersection                               = 0
       same set?                                  = False
```

Set-level confirmation (ids): `permit_date='2026-09-09'` = 22 projects, all
`classification=NEEDS_VERIFICATION` (21 `Not verified`, 1 `Evidence found, status unclear`), so
none is public; the 22 public in-window rows are all dated ≥ 2026-09-10. **Verdict: the two 22s
are different, non-overlapping sets.**

## J1b — pasted breakdowns

**In-window (`>= 2026-09-09`) by classification**

```
   'NEEDS_VERIFICATION'   136
   'MEDIUM'               21
   'HIGH'                 1
```

**In-window by procurement_status**

```
   'Evidence found, status unclear'       101
   'Not verified'                         52
   'Closed'                               5
```

**Exclusion gate for non-public in-window**

```
   classification (not HIGH/MEDIUM)      : 136
   procurement status (not discoverable) : 0
   other / no evidence tier (among public): 0
   --- non-public in-window total        : 136 (= 136 + 0 + 0)
```

**10 sample non-public in-window rows** (name, city, permit date, classification,
procurement_status, tier, reason)

```
   THE VILLAGE AT OWNSBY FARMS RETA | Celina      | 2026-12-02 | NEEDS_VERIFICATION | Not verified           | tier=None | classification=NEEDS_VERIFICATION
   Plumbing remodel for Burnt apart | Dallas      | 2026-10-09 | NEEDS_VERIFICATION | Evidence found, status | tier=None | classification=NEEDS_VERIFICATION
   Plumbing remodel for burnt units | Dallas      | 2026-10-09 | NEEDS_VERIFICATION | Evidence found, status | tier=None | classification=NEEDS_VERIFICATION
   New construction of a general me | Dallas      | 2026-10-09 | NEEDS_VERIFICATION | Evidence found, status | tier=None | classification=NEEDS_VERIFICATION
   Electrical NEW construction      | Dallas      | 2026-10-09 | NEEDS_VERIFICATION | Evidence found, status | tier=None | classification=NEEDS_VERIFICATION
   A 16' x 16'' LED dance floor wil | Dallas      | 2026-10-09 | NEEDS_VERIFICATION | Not verified           | tier=None | classification=NEEDS_VERIFICATION
   Relocating notification devices  | Dallas      | 2026-10-09 | NEEDS_VERIFICATION | Not verified           | tier=None | classification=NEEDS_VERIFICATION
   cafe for employees of Sterling   | Dallas      | 2026-10-09 | NEEDS_VERIFICATION | Evidence found, status | tier=None | classification=NEEDS_VERIFICATION
   Building 6- Foundation Repair- I | Fort Worth  | 2026-10-08 | NEEDS_VERIFICATION | Evidence found, status | tier=None | classification=NEEDS_VERIFICATION
   QTEAM INHOUSE - Property Manager | Dallas      | 2026-10-08 | NEEDS_VERIFICATION | Evidence found, status | tier=None | classification=NEEDS_VERIFICATION
```

**Permit-date distribution by month for all 133 public projects**

```
   2025-12  1
   2026-01  3
   2026-02  4
   2026-03  9
   2026-04  8
   2026-05  26
   2026-06  16
   2026-07  20
   2026-08  21
   2026-09  11
   2026-10  13
   2026-12  1
```

## J1c — should recent unclassified filings get a "New filings, not yet verified" view?

Yes — but only as a separate, plainly labelled surface, never mixed into the verified feed.
Today 136 of the 158 in-window projects are `NEEDS_VERIFICATION` and are excluded from
`/opportunities` by the classification gate (`service.py`, `PUBLIC_CLASS`), so a fresh market
looks empty; a "New filings, not yet verified" view would list those rows under their own
heading, with the evidence tier left empty and rendered as "Not verified", and would state in
one line that classification is pending rather than that the trade is absent. The risk of
showing them is that a contractor reads an unclassified plumbing remodel or a dance-floor
electrical permit as a commercial HVAC opportunity; the risk of hiding them is a product that
looks dead in a new market. The wording risk is the whole game: the page must never say
"HVAC", "opportunity" or "open" about an unverified filing — the noun is "filing", the state is
"not yet verified", and the page must not imply the trade is unknown rather than merely
unclassified. This is a presentation change only; the classification logic is not touched, so
a filing moves from the "not yet verified" view into the verified feed exactly when the
existing classifier promotes it to HIGH/MEDIUM.

## Raw pasted output

See `audit/J1_I1_PASTE.txt` (full stdout of the instrument).
