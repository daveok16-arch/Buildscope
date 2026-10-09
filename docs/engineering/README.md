# Engineering records

These documents are historical records: the platform's design rationale, source
reconnaissance, and audit/remediation reports. They document how BuildScope was built and what
was found at each point in time. They are not current product documentation — for that, start at
the [documentation index](../README.md).

Where a record references a file path that has since moved (many of these were written at the
repository root), the reference is left as it was written, because these are point-in-time
records. The current locations are:

| Record | Subject |
|---|---|
| [DESIGN.md](DESIGN.md) | The original MVP design: architecture, schema, evidence chain, collection strategy, classification scoring |
| [dallas_source.md](dallas_source.md) | Reconnaissance of the Dallas Accela permit endpoint and its request mechanism |
| [POSTGRES_MIGRATION.md](POSTGRES_MIGRATION.md) | A target architecture (PostgreSQL + PostGIS) that is **not implemented** |
| [BUILDSCOPE_PLATFORM_AUDIT.md](BUILDSCOPE_PLATFORM_AUDIT.md) | Platform audit of working, partial and broken behaviour |
| [BUILD_SCOPE_REPOSITORY_AUDIT.md](BUILD_SCOPE_REPOSITORY_AUDIT.md) | Full repository audit, including the committed-key finding |
| [PHASE_1A_AUTH_REMEDIATION.md](PHASE_1A_AUTH_REMEDIATION.md) | Authentication remediation and its verification |
| [PHASE_8_INTELLIGENCE_AUDIT.md](PHASE_8_INTELLIGENCE_AUDIT.md) | Audit preceding the search, trend and LinkedIn capabilities |
