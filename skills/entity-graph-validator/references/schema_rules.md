# Entity Graph Validation Rules

1. **Organization Schema:** The target URL must contain a valid `Organization`, `Brand`, `Corporation`, or `LocalBusiness` JSON-LD block.
2. **Entity Authority (sameAs):** The schema must contain a `sameAs` array referencing at least two external knowledge base URIs (e.g., Wikidata, Crunchbase, official social profiles).
3. **Temporal Freshness:** Product or Article entities must declare `dateModified` timestamps to prevent stale AI factual citations.