# Evidence and classification

The product's value rests on being checkable, so these rules are enforced in code rather than by
convention.

## The evidence standard

- **Never invent.** A project field can only be set through `provenance.assert_field`, which
  simultaneously records the evidence that licenses the value. An unsourced fact cannot be created
  by accident. Source sentinels such as `NULL` are never stored.
- **Missing stays missing.** An unverified field is `NULL` in the database and renders as
  "Not verified" in HTML, or `null` in JSON. The API returns `null` rather than the string, because
  `"architect": "Not verified"` would assert that an architect by that name exists.
- **Every material claim is citable.** Each project carries the source name, the record
  identifier, the source's own date, and the excerpt that supports the value.
- **Contradictions are preserved.** When two publishers state different values for the same field,
  both are retained and the field is flagged. Several permits from one publisher are not a
  contradiction — that is normal scope.

## Classification

Classification answers *"how strong is the evidence?"* — a property of the record, computed once
during assembly and stored. Each project is HIGH, MEDIUM or NEEDS VERIFICATION, with the reasons
that produced the score recorded per point. Four gates apply, and the mechanical-evidence gate is
the important one:

- **Trade evidence is required for discovery.** A trade directory lists only projects with
  evidence of that trade. The requirement comes from `config/trades.yaml`, so a future trade
  declares its own evidence column and values.
- **Completed work is not an opportunity.** A project whose source status records the work as
  finished is excluded from discovery. It stays reachable by direct link, where its status is
  shown plainly and the page warns that the mechanical scope has already been let or completed.

## Procurement

Procurement is never implied. Four states exist, and `Confirmed open` is unreachable because no
source in this market publishes bid status. Every customer-facing page states verbatim that permit
evidence does not confirm an available HVAC package.

## Why the classified total and the discoverable total differ

Two different numbers describe "HIGH", and they are supposed to differ. This is the pipeline
working, not a discrepancy to reconcile away.

- The **classified** count comes from `project.classification` in the intelligence database —
  projects the classification gates judged on their evidence, and it includes work that is
  finished.
- The **discoverable** count is what `/opportunities`, the API and the statistics endpoint expose —
  the subset that is currently worth showing a customer.

The difference is the procurement and trade-evidence filters, and nothing else. Classification
answers a property of the record; discovery answers a property of the moment, which also depends on
whether the work is still live and whether it matches the trade being served. Keeping them separate
means the audit view (which lists every classified project) and the customer view (which lists the
discoverable ones) can both be correct at the same time. Re-classifying closed work, or showing
finished buildings so the numbers match, would destroy the distinction the product depends on.

The precise figures change as ingestion runs, so the current counts live in the generated reports,
not here:

```bash
python -m oppintel.cli report      # writes reports/out/{coverage,data_quality,validation}_report.md
```

## The funnel

Discovery is a sequence of filters, each with a stated purpose. No step changes a classification.

```
permit records                     collected from the configured public sources
   ↓  assembled by address
projects                           permits grouped into the underlying project
   ↓  classification gates
HIGH / MEDIUM projects             scored on documented evidence
   ↓  procurement filter           excludes closed and not-verified work
projects with active procurement   work recorded as proceeding
   ↓  trade evidence filter        config/trades.yaml
discoverable opportunities         evidence of the served trade present
```

## Where the filters live, and why

The classification gates live in the intelligence layer (`classify.py`). The discovery filters
live in the application layer (`eligibility.py`, `procurement.py`, `service.py`). This is asserted
by `tests/test_app_architecture.py`. Moving a discovery filter into classification — or vice versa
— would let a page and a report disagree, so the boundary is a test, not a convention.
