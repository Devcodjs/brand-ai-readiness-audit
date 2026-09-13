# Entity Graph & Structured-Data Validation Rules

Reference file for the **entity-graph-validator** skill (part of the `brand-ai-readiness-audit`
marketplace). Loaded on demand — `SKILL.md` should point here for the full rule catalog rather
than inlining it, and `scripts/validate_entity.py` should treat this file as the source of truth
for rule IDs, severities, and evidence phrasing.

## Table of Contents

1. [Purpose — why this skill exists](#1-purpose--why-this-skill-exists)
2. [Core concept: the entity graph](#2-core-concept-the-entity-graph)
3. [Scope boundary — what this skill owns vs. hands off](#3-scope-boundary--what-this-skill-owns-vs-hands-off)
4. [Entity types in scope](#4-entity-types-in-scope)
5. [Rule catalog](#5-rule-catalog)
   - 5.1 [Presence & syntax — EG-1xx](#51-presence--syntax--eg-1xx)
   - 5.2 [Canonical identity (`@id`) — EG-2xx](#52-canonical-identity-id--eg-2xx)
   - 5.3 [Graph connectivity — EG-3xx](#53-graph-connectivity--eg-3xx)
   - 5.4 [Disambiguation & corroboration — EG-4xx](#54-disambiguation--corroboration--eg-4xx)
   - 5.5 [Cross-page consistency — EG-5xx](#55-cross-page-consistency--eg-5xx)
   - 5.6 [Duplication & fragmentation — EG-6xx](#56-duplication--fragmentation--eg-6xx)
   - 5.7 [Render-timing handoff — EG-7xx](#57-render-timing-handoff--eg-7xx)
   - 5.8 [Proactive enhancements — EG-8xx](#58-proactive-enhancements--eg-8xx)
6. [The generic name-collision heuristic](#6-the-generic-name-collision-heuristic)
7. [Severity rubric](#7-severity-rubric)
8. [False-positive guardrails](#8-false-positive-guardrails)
9. [Evidence & output formatting conventions](#9-evidence--output-formatting-conventions)
10. [Quick-reference checklist](#10-quick-reference-checklist)

---

## 1. Purpose — why this skill exists

Two mechanisms from the Round-2 reasoning are relevant here, and it's worth keeping both in view
rather than treating this as a generic "check for JSON-LD" pass:

- **Explicitness beats implication.** A machine reading a page extracts facts more reliably the
  more explicitly and unambiguously they're stated in a structured, readable form. Schema.org/
  JSON-LD is the most explicit form a page can offer — it's the difference between a fact a model
  has to infer from prose and a fact it can read off a labeled field.
- **Mistaken identity is a real failure mode.** When a name is shared by more than one real-world
  thing, an assistant can conflate them unless something clearly distinguishes one from the
  others. The fix isn't just "have structured data" — it's structured data that is internally
  consistent, connected into a single graph, and corroborated by independent, authoritative
  external references.

So this skill isn't just "does `<script type="application/ld+json">` exist." It's: *does the
site describe itself as one clear, well-connected, externally-corroborated entity, or as a loose
pile of disconnected, inconsistent, or generic fragments that a system has to guess its way
through?*

Everything below should be read as reasoning about that question, not as a checklist to pattern-
match against specific sites. Findings must generalize — a rule that only fires on one observed
site's quirks belongs in evidence text, not in the rule catalog.

## 2. Core concept: the entity graph

A well-formed site describes itself as a **graph of nodes**, not a bag of independent objects:

- Every real-world thing that matters (the company, its website, its products, its authors, its
  physical locations) gets **one canonical node**, addressed by a stable `@id`.
- Other nodes **reference** that node by `@id` instead of re-describing it inline. A `WebSite`
  node should point at the `Organization` node as its `publisher`; a `Product` should point at the
  `Organization`/`Brand` node; an `Article` should point at both a `Person` (author) and an
  `Organization` (publisher).
- The canonical node carries **disambiguating** properties (`sameAs`, `foundingDate`, `address`,
  `alternateName`, `logo`, etc.) that let an outside system confirm *which* "Acme" this is.

This is the same logic as a knowledge graph: nodes + typed edges + external corroboration. A site
that repeats slightly different, disconnected blobs of the same entity on every page hasn't built
a graph — it's built noise that happens to be structured. That distinction is what most of the
rules below are actually testing for, even when the individual check looks like a simple presence
check.

## 3. Scope boundary — what this skill owns vs. hands off

The marketplace splits the Round-2 failure modes across multiple skills. To keep findings
non-redundant, this skill owns the **structured-data identity & disambiguation layer** — not
every adjacent concern. When a check would clearly belong to another skill, flag it lightly (or
not at all) and let that skill own the finding:

| Adjacent concern | Owned by | This skill's role |
|---|---|---|
| Whether a crawler can reach the page at all | `network-accessibility-audit` | Assumes the page was reachable; doesn't re-check robots.txt/status codes. |
| Facts trapped in images/canvas/video instead of text | `content-extractability-audit` | Only checks whether structured-data *fields themselves* are text/typed, not prose extractability generally. |
| Whether JSON-LD is present in the raw HTML vs. only after JS execution | `crawl-render-audit` (mechanics) | This skill **detects and flags** the symptom (EG-7xx) but defers the render-pipeline diagnosis to that skill. |
| Whether the same NAP (name/address/phone) appears consistently across *external* directories, maps, and listings | `local-omnichannel-audit` | This skill checks *internal* graph consistency and the presence of `sameAs`/`address` fields; it does not fetch third-party directories itself. |
| Whether facts are stale or only corroborated by outdated sources | `content-freshness-audit` | This skill checks that corroborating links *exist and resolve*, not that they're current. |
| Category/taxonomy structure and internal silo boundaries | `category-isolation-audit` | This skill checks `BreadcrumbList` only as an entity-graph signal (does it connect to real category nodes), not for taxonomy design quality. |

If the orchestrator later needs to de-duplicate overlapping findings across skills, evidence
strings from this skill should always be scoped to "the JSON-LD graph said X" so it's obvious
which layer produced the finding.

## 4. Entity types in scope

For each type: the properties that should always be present ("Required"), the properties that
meaningfully strengthen disambiguation/connectivity when present ("Strengthens graph"), and the
canonical linking property other nodes use to point at it.

| Type | Required | Strengthens graph | Referenced via |
|---|---|---|---|
| `Organization` (or a subtype like `Corporation`) | `@id`, `name`, `url`, `logo` | `sameAs[]`, `foundingDate`, `alternateName`, `description`, `contactPoint`, `address` | `publisher`, `brand`, `worksFor`, `parentOrganization` |
| `LocalBusiness` (or subtype, e.g. `Store`, `Restaurant`) | `@id`, `name`, `address` (as `PostalAddress`), `url` | `sameAs[]`, `geo`, `openingHoursSpecification`, `telephone`, `parentOrganization`/`branchOf` → `Organization @id` | `location` |
| `WebSite` | `@id`, `name`, `url`, `publisher` (→ `Organization @id`) | `potentialAction` (SearchAction), `inLanguage` | `isPartOf` (from `WebPage`) |
| `Product` | `@id`, `name`, `brand` (→ `Organization`/`Brand @id`), `offers` (`Offer`/`AggregateOffer`) | `sku`/`gtin`/`mpn`, `image`, `aggregateRating`, `review` | `mainEntity` |
| `Person` (authors, execs) | `name` | `sameAs[]`, `worksFor` (→ `Organization @id`), `jobTitle`, `url` | `author`, `founder`, `employee` |
| `Article` / `BlogPosting` / `NewsArticle` | `headline`, `author` (→ `Person @id`), `publisher` (→ `Organization @id`), `datePublished` | `dateModified`, `mainEntityOfPage` | — |
| `BreadcrumbList` | `itemListElement[]` each with `position`, `name`, `item` (URL) | Matches the visible on-page breadcrumb exactly | — |
| `FAQPage` / `QAPage` | `mainEntity[]` of `Question`, each with `acceptedAnswer` (`Answer`) | Answer text matches the visible on-page answer verbatim | — |
| `AggregateRating` / `Review` | `ratingValue`, `reviewCount`/`ratingCount` (for `AggregateRating`) | `itemReviewed` (→ correct `@id`), `author` | attached to `Product`/`LocalBusiness`/`Organization` |

This list is deliberately not exhaustive — if a site's primary offering doesn't fit one of these
types (e.g. `Event`, `JobPosting`, `SoftwareApplication`), apply the same underlying rules
(identity, connectivity, disambiguation) to whatever the closest matching schema.org type is
rather than skipping the page.

## 5. Rule catalog

Each rule below is written as: what to check, why it matters (mechanism, not superstition), what
evidence to record, a default severity (a starting point — see §7 for how to adjust it), and a
suggested-action template. Rule IDs are stable identifiers; use them as the finding `id` prefix
(e.g. `EG-201`) so the same defect reported on different sites is traceable to the same root
cause.

### 5.1 Presence & syntax — EG-1xx

**EG-101 — No JSON-LD anywhere on a page type that warrants it**
- *Check:* Page is a homepage, product page, article, or location page and contains zero
  `<script type="application/ld+json">` blocks (checked against the raw HTML response, not just
  the rendered DOM — see EG-701).
- *Why it matters:* This is the single largest gap. Without any structured data, every fact about
  the entity has to be inferred from prose, which is strictly less reliable for extraction than a
  labeled field.
- *Evidence:* `"Crawled N {page-type} pages; 0/N contain a JSON-LD block."`
- *Default severity:* High (Critical if it's the homepage or every product page).
- *Suggested action:* Add a minimal `Organization`/`WebSite` block sitewide and page-appropriate
  types (`Product`, `Article`, etc.) per page.

**EG-102 — Invalid JSON syntax**
- *Check:* The contents of a `application/ld+json` block do not parse as valid JSON.
- *Why it matters:* A parser error means the entire block is silently discarded by anything that
  actually validates it — the site gets zero credit for a near-miss.
- *Evidence:* Parser error message + byte offset + URL.
- *Default severity:* Critical (this is a hard failure, not a partial one).
- *Suggested action:* Fix the syntax error; add JSON-LD to a CI/build-time linter so this can't
  regress silently.

**EG-103 — Missing or malformed `@context`**
- *Check:* `@context` is absent, or doesn't resolve to the schema.org vocabulary (accept both
  `https://schema.org` and `http://schema.org` — schema.org itself treats these as equivalent, so
  do not flag the scheme alone; only flag genuinely wrong/misspelled hosts, e.g. `schema.og`).
- *Why it matters:* Without a resolvable context, `@type` values are just uninterpreted strings.
- *Evidence:* The literal `@context` value found (or "absent").
- *Default severity:* High.
- *Suggested action:* Set `"@context": "https://schema.org"` at the top level of each block.

**EG-104 — `@type` too generic for the content**
- *Check:* The only type present is a broad/uninformative one (`Thing`, `WebPage`, `CreativeWork`)
  on a page that's clearly a product, article, or business-location page.
- *Why it matters:* A generic type gives an extractor almost nothing to key off; the specific
  subtype is what unlocks the properties that actually disambiguate and describe the entity.
- *Evidence:* Page URL, detected content type (from headings/URL pattern), `@type` found.
- *Default severity:* Medium.
- *Suggested action:* Use the most specific applicable schema.org type (e.g. `Product` instead of
  `Thing`, `NewsArticle` instead of `WebPage`).

**EG-105 — Required properties missing for the declared type**
- *Check:* A node declares a type from §4 but omits one or more of that type's "Required" columns.
- *Why it matters:* A structurally valid but incomplete node still leaves the disambiguating gaps
  that cause conflation — e.g. an `Organization` with no `@id` can't be referenced by anything
  else on the site (see 5.2).
- *Evidence:* `@type` + list of missing required properties + URL.
- *Default severity:* Medium–High depending on which property (missing `@id` or `name` is higher
  than missing `logo`).
- *Suggested action:* Add the specific missing field(s); list them by name in the fix, don't just
  say "complete the schema."

### 5.2 Canonical identity (`@id`) — EG-2xx

**EG-201 — Primary entity has no `@id`**
- *Check:* The site's core `Organization` (or primary `LocalBusiness`/`Product`) node has no
  `@id` field.
- *Why it matters:* `@id` is what turns a node into an addressable graph member. Without it, every
  other node that should reference it (`WebSite.publisher`, `Product.brand`, `Article.publisher`)
  is forced to either omit the link or re-embed a full copy — which is exactly the fragmentation
  problem in 5.6.
- *Evidence:* Node's `@type`/`name`, URL(s) where it appears without `@id`.
- *Default severity:* High.
- *Suggested action:* Assign a stable canonical `@id`, conventionally
  `https://example.com/#organization`, and reuse it verbatim everywhere the entity is referenced.

**EG-202 — `@id` is not stable across pages**
- *Check:* The same real-world entity (same `name`, same `logo`) appears with a *different* `@id`
  value on different pages (e.g. `#organization` on the homepage, `#org` on the about page,
  `https://example.com/about/#business` elsewhere).
- *Why it matters:* Each distinct `@id` is a distinct node as far as a graph consumer is
  concerned. An unstable `@id` silently recreates the fragmentation problem even though every
  individual page "has" structured data.
- *Evidence:* List of distinct `@id` values found for what is evidently the same entity, with the
  pages each appeared on.
- *Default severity:* High.
- *Suggested action:* Standardize on one `@id` string and template it site-wide rather than
  hand-authoring it per page.

**EG-203 — `@id` uses a non-canonical/non-resolvable URL**
- *Check:* `@id` points at a URL that 404s, redirects, or uses a non-canonical domain/protocol
  variant (`http://` when the site serves `https://`, or a `www.`/non-`www.` mismatch vs. the
  site's declared canonical).
- *Why it matters:* `@id` doesn't have to be dereferenceable, but using the canonical URL (plus a
  `#fragment`) as convention makes the identity trivially verifiable and avoids accidental
  collisions with unrelated sites reusing a generic fragment like `#org`.
- *Evidence:* `@id` value + what it resolves to (or fails to).
- *Default severity:* Low–Medium.
- *Suggested action:* Base `@id` on the canonical domain the site itself declares (`<link
  rel="canonical">` or the sitemap's host).

**EG-204 — Blank-node references that are never defined**
- *Check:* A property references `{"@id": "#something"}` but no node with that `@id` is ever fully
  defined anywhere in the page's (or site's) JSON-LD graph.
- *Why it matters:* A dangling reference is a broken edge — the consuming system either drops it
  or has to guess, both of which erase the connectivity the site presumably intended.
- *Evidence:* The dangling reference, the page it appears on, and the fact that no matching full
  node was found.
- *Default severity:* Medium.
- *Suggested action:* Either fully define the referenced node on that page, or use a `@graph`
  array so definitions and references stay together.

### 5.3 Graph connectivity — EG-3xx

**EG-301 — `WebSite.publisher` is inline/duplicated instead of `@id`-referenced**
- *Check:* `WebSite` node's `publisher` is a fully inlined `Organization` object rather than an
  `{"@id": "..."}` pointer to the canonical `Organization` node.
- *Why it matters:* Inlining creates a second, disconnected copy of the organization's facts,
  which can drift out of sync with the canonical node (see 5.5/5.6) and gives a graph consumer two
  candidate nodes instead of one confirmed one.
- *Evidence:* URL, the inlined object's field values vs. the canonical node's.
- *Default severity:* Medium.
- *Suggested action:* Replace the inline object with `{"@id": "https://example.com/#organization"}`.

**EG-302 — `Product.brand` is a bare string or unlinked `Brand`, never connected to `Organization`**
- *Check:* `Product.brand` is a plain string, or a `Brand`/`Organization` object with no `@id` tying
  it back to the canonical company node.
- *Why it matters:* Without the link, a system reading the product page has no way to connect
  "this product" to "the company that makes it and everything corroborated about that company"
  (reviews, `sameAs`, founding facts, etc.) — the product is an island.
- *Evidence:* Product URL + the `brand` value found.
- *Default severity:* Medium.
- *Suggested action:* Set `brand` to `{"@id": "https://example.com/#organization"}` (or a `Brand`
  node that itself carries that `@id`, if brand ≠ company legal entity).

**EG-303 — `Article`/`BlogPosting` author and publisher aren't both linked**
- *Check:* Article-type content is missing either an `author` → `Person`/`Organization` link, a
  `publisher` → `Organization` link, or both are present but as unlinked inline strings.
- *Why it matters:* Authorship and publication are two of the clearest trust/provenance signals a
  content page can offer; leaving them as plain strings prevents cross-referencing the same author
  or publisher across other articles and pages.
- *Evidence:* Article URL, which of `author`/`publisher` is missing or unlinked.
- *Default severity:* Medium.
- *Suggested action:* Add both, each pointing at a canonical `@id`-bearing node.

**EG-304 — Multi-location business has no `Organization`↔`LocalBusiness` linkage**
- *Check:* Site has multiple location pages each with a `LocalBusiness` node, but none declare
  `parentOrganization`/`branchOf` pointing back to the canonical `Organization @id`.
- *Why it matters:* Without the link, each location looks like an independent, same-named business
  rather than branches of one entity — which is precisely the "several different things share a
  name" ambiguity, self-inflicted.
- *Evidence:* Count of location pages found, confirmation none link upward.
- *Default severity:* High (this is a common and consequential omission for multi-location
  brands).
- *Suggested action:* Add `"branchOf": {"@id": "https://example.com/#organization"}` to every
  `LocalBusiness` node.

**EG-305 — `BreadcrumbList` doesn't match the page's actual category placement**
- *Check:* The `BreadcrumbList` JSON-LD exists but its `item` URLs/names don't match the visible
  on-page breadcrumb trail, or terminate before reaching the current page.
- *Why it matters:* A mismatched breadcrumb graph misrepresents how this entity/page relates to
  the site's category structure, which is itself a disambiguating signal (e.g. "this is the
  camera lens 'Otus', in Photography > Lenses," not some other Otus).
- *Evidence:* On-page breadcrumb text vs. JSON-LD `itemListElement` contents, URL.
- *Default severity:* Low–Medium.
- *Suggested action:* Generate `BreadcrumbList` from the same source of truth as the rendered
  breadcrumb UI so they can't drift apart.

### 5.4 Disambiguation & corroboration — EG-4xx

**EG-401 — No `sameAs` on the primary entity at all**
- *Check:* Canonical `Organization`/`LocalBusiness` node has no `sameAs` array.
- *Why it matters:* `sameAs` is the mechanism for external corroboration — it's how a consuming
  system checks "does an independent, authoritative source agree this entity exists and is
  described this way." Zero corroborating links means the entity's identity rests entirely on the
  brand's own say-so.
- *Evidence:* Confirmation `sameAs` is absent; note whether corroborating profiles clearly exist
  elsewhere (e.g. a linked LinkedIn/X icon in the page footer) but simply weren't encoded.
- *Default severity:* Medium (see §8 — don't over-penalize small/new entities that genuinely lack
  authoritative profiles).
- *Suggested action:* Add `sameAs` links to whichever of the entity's official profiles genuinely
  exist, prioritized per the authority ordering below.

**EG-402 — `sameAs` links exist but only to low-authority or unrelated sources**
- *Check:* `sameAs` is present but every entry is something like an internal subdomain, a generic
  aggregator listing, or a URL that 404s/redirects to something unrelated.
- *Why it matters:* Not all corroboration is equal. A rough priority order, most authoritative
  first: (1) Wikidata, (2) Wikipedia, (3) official social profiles under the brand's own verified
  handle, (4) recognized industry/registry directories (Crunchbase, G2/Capterra for software,
  chamber-of-commerce or BBB-style registries for local business), (5) app-store listings. Links
  that don't fall into any of these tiers add little.
- *Evidence:* The `sameAs` URLs found, categorized by tier (or "uncategorized/broken").
- *Default severity:* Low–Medium.
- *Suggested action:* Replace or supplement with higher-tier links; verify each resolves to a page
  that actually names this entity.

**EG-403 — Generic/ambiguous name with no compensating disambiguation signals**
- *Check:* See the heuristic in §6. If the entity name is flagged as generic/collision-prone, and
  the node lacks at least two of: `foundingDate`, structured `address`, `alternateName`,
  `description` naming the specific industry/niche, `sameAs` to Wikidata/Wikipedia — flag it.
- *Why it matters:* This is the direct, mechanism-level version of the "mistaken identity"
  problem: a generic name with no distinguishing structured facts is exactly the case where an
  assistant is most likely to answer about the wrong "Apex" or "Meridian."
- *Evidence:* The name, the heuristic signal that triggered the flag, which distinguishing fields
  are present vs. missing.
- *Default severity:* High.
- *Suggested action:* Add the missing distinguishing fields; if no Wikidata/Wikipedia entry
  exists, note that as a proactive action (EG-802) rather than a defect the brand can fix
  instantly.

**EG-404 — Conflicting distinguishing facts across the site's own pages**
- *Check:* `foundingDate`, `address`, or `name`/`alternateName` for the canonical entity differs
  between two pages that both claim to describe it.
- *Why it matters:* Internal disagreement is worse than silence — it actively teaches a corroboration-
  seeking system that this entity's facts are unreliable, which undermines trust in every other
  fact on the site too.
- *Evidence:* The two (or more) conflicting values and the pages they came from.
- *Default severity:* Critical.
- *Suggested action:* Establish one source of truth for these fields (e.g. a shared data/config
  file feeding every template) and regenerate all pages from it.

**EG-405 — `LocalBusiness.address` is an unstructured string instead of `PostalAddress`**
- *Check:* `address` is a plain string rather than a `PostalAddress` object with
  `streetAddress`/`addressLocality`/`addressRegion`/`postalCode`/`addressCountry`.
- *Why it matters:* A free-text address is exactly the "implied, not explicit" case — a human
  reads it fine, but a system has to parse unstructured text to get the fields it actually needs
  (e.g. to match this location against a maps/directory listing for corroboration).
- *Evidence:* The literal `address` value found.
- *Default severity:* Medium.
- *Suggested action:* Convert to a `PostalAddress` object with each component field populated.

**EG-406 — Author `Person` nodes have no disambiguation for common names**
- *Check:* An `author` is a `Person` with only a `name` (no `sameAs`, `worksFor`, or `url`), and
  that name is common enough to plausibly collide (see §6's heuristic, applied to person names:
  short, no middle name/initial, no unique qualifier).
- *Why it matters:* Same mechanism as EG-403, applied to authorship — attribution is only useful
  for trust if it's attributable to a specific, identifiable person.
- *Evidence:* Author name, articles it appears on, confirmation no linking fields are present.
- *Default severity:* Low–Medium.
- *Suggested action:* Link `worksFor` to the canonical `Organization @id` and add `sameAs` to the
  author's professional profile.

### 5.5 Cross-page consistency — EG-5xx

**EG-501 — `name`/`legalName` varies across pages without `alternateName` reconciling it**
- *Check:* The canonical entity's `name` differs across pages (e.g. "Acme Inc.", "Acme
  Corporation", "ACME") and the variants aren't declared as `alternateName` on a single canonical
  node.
- *Why it matters:* Variant naming is normal and fine — what breaks disambiguation is variants
  that aren't reconciled into one graph node, which looks like several different entities rather
  than one entity with known aliases.
- *Evidence:* The distinct name strings found and where.
- *Default severity:* Medium.
- *Suggested action:* Pick one canonical `name`, list the rest under `alternateName` on the same
  `@id`-bearing node.

**EG-502 — `logo` differs across pages**
- *Check:* Different `logo` URLs/images are declared for what should be the same `Organization`
  node on different pages.
- *Why it matters:* Visual identity is one of the simplest cross-checks a corroborating system can
  do; inconsistency here is a cheap, avoidable red flag.
- *Evidence:* The distinct `logo` values and pages.
- *Default severity:* Low.
- *Suggested action:* Reference one canonical `logo` `ImageObject` (with `url`, `width`,
  `height`) from every page, ideally via the shared `@id` node rather than re-declaring it.

**EG-503 — `sameAs` set differs across pages for the same entity**
- *Check:* One page's `Organization` node lists a different (not just differently-ordered)
  `sameAs` set than another page's node for what is evidently the same entity.
- *Why it matters:* This usually indicates the entity is being hand-authored per-template rather
  than sourced from one canonical record — a strong signal that other fields are drifting too, per
  EG-404.
- *Evidence:* The two `sameAs` sets and their pages.
- *Default severity:* Medium.
- *Suggested action:* Same remedy as EG-404 — single source of truth, templated everywhere.

### 5.6 Duplication & fragmentation — EG-6xx

**EG-601 — Multiple distinct `@id`s for what is clearly one real-world entity**
- *Check:* Two or more nodes with different `@id` values but matching `name`, `logo`, and/or
  `address` — i.e., the same entity described as if it were several.
- *Why it matters:* This is fragmentation's clearest form: instead of one well-corroborated node,
  the site presents several thinner, disconnected ones, diluting whatever `sameAs`/trust signals
  each partial node carries.
- *Evidence:* The distinct `@id`s, the matching fields that indicate they're the same entity.
- *Default severity:* High.
- *Suggested action:* Merge into a single canonical `@id` and update every reference site-wide.

**EG-602 — Same-named entity used for two genuinely different things without disambiguating type/context**
- *Check:* The reverse of EG-601 — the same `name` is legitimately used for two different real
  entities on the site (e.g. a product line and an unrelated internal tool share a name) but
  nothing (distinct `@id`, distinct `@type`, `disambiguatingDescription`) marks them apart.
- *Why it matters:* This is the site creating its own internal version of the "mistaken identity"
  problem, which then propagates outward.
- *Evidence:* The shared name, the two distinct things it refers to, confirmation no
  disambiguating field exists.
- *Default severity:* Medium.
- *Suggested action:* Add `alternateName` or a short `disambiguatingDescription` to each, and keep
  their `@id`s distinct and stable.

**EG-603 — Duplicate JSON-LD blocks on one page disagree with each other**
- *Check:* A single page contains more than one `application/ld+json` block declaring the same
  `@type`/`@id` with different field values (commonly caused by a CMS plugin and a manually-added
  block both injecting an `Organization` node).
- *Why it matters:* This is an internal contradiction visible to any consumer that reads the whole
  page — the site is disagreeing with itself within a single HTTP response.
- *Evidence:* Both block contents, the specific fields that conflict.
- *Default severity:* High.
- *Suggested action:* Consolidate to a single block per node per page; audit CMS plugins for
  overlapping schema injection.

### 5.7 Render-timing handoff — EG-7xx

**EG-701 — JSON-LD present in the rendered DOM but absent from the raw HTML response**
- *Check:* Fetching the page's HTML directly (no JS execution) shows no `application/ld+json`
  block that a headless-browser render does show.
- *Why it matters:* Many systems that build answers from web content fetch pages the lightweight
  way, without executing JavaScript. Structured data that only exists after client-side rendering
  is invisible to exactly the class of consumer this whole audit cares about — even though a human
  (and a full browser-based crawler) would see it fine.
- *Evidence:* Confirmation of presence in rendered DOM vs. absence in raw response, for the same
  URL.
- *Default severity:* High.
- *Suggested action:* Server-side render (or statically inject) the JSON-LD block so it's present
  in the initial HTML response; this is a `crawl-render-audit` root cause — cross-reference rather
  than duplicate the fix guidance.

**EG-702 — JSON-LD injected via `document.write` or a delayed async script**
- *Check:* The JSON-LD block is added by a script that runs after a delay, on a user interaction,
  or via `document.write` (which many modern crawlers explicitly disable).
- *Why it matters:* Same mechanism as EG-701 but with an identifiable specific cause worth naming
  in the fix.
- *Evidence:* The injection method observed (script tag position/attributes, timing).
- *Default severity:* Medium–High.
- *Suggested action:* Move the JSON-LD into a synchronous, render-blocking `<script>` tag emitted
  in the initial HTML.

### 5.8 Proactive enhancements — EG-8xx

These fire even when no defect was found — they're the "strengthen it further" suggestions the
brief explicitly asks for.

**EG-801 — Add `ContactPoint` for direct trust signals**
- *Suggested action:* Add a `ContactPoint` (with `contactType`, `telephone`/`email`) to the
  canonical `Organization` node — a small addition that gives a consuming system a verifiable
  contact channel to corroborate against.

**EG-802 — Pursue a Wikidata entry if the brand is notable enough and lacks one**
- *Suggested action:* If the brand has genuine independent coverage (press, industry recognition)
  but no Wikidata item, creating one (with `sameAs` pointing to it) is disproportionately valuable
  since Wikidata is the highest-authority corroboration source many systems consult.

**EG-803 — Add `AggregateRating`/`Review` where genuine reviews exist but aren't marked up**
- *Suggested action:* If the site displays review counts/stars in the UI but doesn't encode them
  as `AggregateRating`, add it — this is visible, real evidence already being shown to humans that
  simply isn't being exposed structurally.

**EG-804 — Add `knowsAbout`/`areaServed`/industry classification to the `Organization` node**
- *Suggested action:* For entities in a crowded namespace, an explicit topical/geographic scope
  field is a cheap additional disambiguator beyond what §6 requires as a minimum.

**EG-805 — Add `Person` nodes (with `sameAs`) for named founders/executives even where not legally
required**
- *Suggested action:* Founder/executive entities strengthen the corroboration graph (a well-known
  person `worksFor` the org is itself a disambiguating signal) and are commonly omitted even by
  otherwise-thorough sites.

## 6. The generic name-collision heuristic

Because this skill must generalize to unseen sites without a hardcoded list of "which names are
ambiguous," use this deterministic proxy rather than an external lookup:

1. Split the entity's `name` into tokens. Flag as **collision-prone** if any of the following
   hold:
   - The name is a single common dictionary word or a short combination of common words (e.g.
     "Bloom," "Apex," "Meridian," "Bridge") rather than a coined/invented term.
   - The name is ≤ 2 words and contains no proper-noun-like distinguishing element (no founder
     surname, no invented term, no geographic qualifier).
   - A quick check of the entity's own `sameAs`/description shows the industry is a common one
     (software, coffee, consulting, fitness) where generic-word naming is common practice —
     i.e., generic name **and** generic/crowded industry compounds the risk.
2. If flagged, the bar in EG-403 applies: require at least two independent distinguishing fields
   beyond the bare name before treating the entity's identity as adequately disambiguated.
3. If **not** flagged (the name is clearly coined/unique, e.g. an invented brand word), do not
   penalize for missing distinguishing fields under this heuristic — treat EG-401/EG-402 (general
   corroboration) as sufficient on their own.

This is intentionally a heuristic, not a certainty — when in doubt, prefer under-flagging over
inventing a false positive (see §8).

## 7. Severity rubric

| Severity | Meaning for this skill |
|---|---|
| **Critical** | The graph actively contradicts itself (EG-404, EG-603) or structured data is present but unparseable (EG-102) — actively harmful, not just absent. |
| **High** | A core identity/connectivity mechanism is missing entirely for the primary entity (`@id`, `sameAs` on a collision-prone name, multi-location linkage, render-timing invisibility). |
| **Medium** | The mechanism exists but is weak, partial, or inconsistent (generic `@type`, unlinked inline duplication, unstructured address). |
| **Low** | Cosmetic or best-practice gaps unlikely to change whether the entity is correctly identified (logo URL drift, non-canonical `@id` URL scheme). |

Adjust the default severities in §5 up or down based on:
- **Page importance** — the same gap on the homepage/primary `Organization` node outranks the
  identical gap on a minor page.
- **Blast radius** — a sitewide templated issue (affects every product page) outranks a one-off.
- **Compounding** — an entity that is both collision-prone (§6) *and* missing corroboration is
  worse than either alone; don't just report both findings independently without noting the
  compounding risk in the evidence text.

## 8. False-positive guardrails

Do **not** flag the following — they're normal, not defects:

- A small or newly-founded business lacking a Wikidata/Wikipedia entry. Note it as a proactive
  suggestion (EG-802) at low severity, never as a high/critical finding — most legitimate
  businesses will never have one, and that's fine.
- `sameAs` linking only to industry-appropriate directories (not Wikidata) when the entity's
  industry doesn't typically get Wikidata coverage (e.g. a local restaurant). Judge the *tier*
  relative to what's realistically obtainable, not against an absolute ceiling.
- A single-location business with only one `LocalBusiness`/`Organization` node and no
  `branchOf` — EG-304 only applies when *multiple* location pages exist.
- Legitimate use of `http://schema.org` as `@context` (schema.org treats http/https as
  equivalent) — don't flag the scheme itself, only genuinely broken/misspelled contexts.
- A brand deliberately using different `name` strings for genuinely different sub-brands/product
  lines it owns, *when* each is properly disambiguated with its own `@id` and a
  `parentOrganization`/`brand` link back to the parent. That's correct graph structure, not
  fragmentation — don't confuse it with EG-601.
- Minor, clearly cosmetic differences in `logo` image *dimensions* (not identity) across pages
  (e.g. a square vs. wide logo variant for different placements) — only flag when the underlying
  entity depicted actually differs.

When a check's outcome is ambiguous under these guardrails, prefer omitting the finding or
lowering its severity and noting the uncertainty in the evidence text, over reporting a confident
false positive. The rubric explicitly penalizes false positives as much as it rewards catches.

## 9. Evidence & output formatting conventions

Every finding this skill contributes to the entrypoint's final report should follow the shared
schema's minimum shape:

```json
{
  "id": "EG-201",
  "title": "Primary Organization entity has no @id",
  "severity": "high",
  "evidence": "Homepage and 8/8 crawled pages declare an Organization node (name: 'Acme Corp') with no @id field, so no other node on the site can reference it.",
  "suggested_action": {
    "summary": "Add \"@id\": \"https://acme.com/#organization\" to the canonical Organization node and reference it (not re-declare it) from WebSite.publisher, Product.brand, and Article.publisher sitewide.",
    "priority": "high"
  }
}
```

Conventions to keep consistent:

- **`id`** — always the catalog rule ID from §5 (e.g. `EG-403`), even if multiple instances of the
  same rule fire on different pages; aggregate them into one finding with a evidence string that
  enumerates the instances, rather than emitting near-duplicate findings.
- **`title`** — restate the rule's check in plain language, specific to what was actually found
  (not the generic rule name verbatim).
- **`evidence`** — always include counts (`N/M pages`), concrete values found (actual `@id`
  strings, actual name variants), and enough specificity that a reader could verify the finding
  themselves by looking at the cited pages.
- **`suggested_action.summary`** — always name the specific field/value to change, not just "fix
  the structured data."
- **`suggested_action.priority`** — mirror the finding's `severity` unless there's a reason they
  should diverge (e.g. a critical finding that's trivial to fix might still get top priority; note
  the reasoning if priority and severity differ).

## 10. Quick-reference checklist

For a fast first pass before consulting the full catalog above:

- [ ] Does the primary entity have a stable `@id`, reused everywhere it's referenced?
- [ ] Does every other node (`WebSite`, `Product`, `Article`) reference that `@id` instead of
      re-embedding a copy?
- [ ] Does the primary entity have `sameAs` links, and are they to genuinely authoritative,
      resolving sources?
- [ ] Is the entity's name generic/collision-prone (§6)? If so, are at least two distinguishing
      fields present?
- [ ] Do `name`, `logo`, `address`, `sameAs`, and `foundingDate` agree across every page that
      declares them?
- [ ] For multi-location brands: does every `LocalBusiness` link back to the parent
      `Organization`?
- [ ] Is the JSON-LD actually present in the raw HTML response, not only after JS execution?
- [ ] Are there any duplicate or self-contradicting JSON-LD blocks on the same page?