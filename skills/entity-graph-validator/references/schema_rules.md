# Entity Graph Schema Rules

1. **Organization Definition:** Every site must expose an `@type`: `Organization` or `Brand` in JSON-LD.
2. **Authority Linking:** Must supply `sameAs` arrays with at least 2 external links (Wikidata, Crunchbase, official social platforms).
3. **Data Completeness:** Must include `name`, `url`, and `logo` properties within the central node.