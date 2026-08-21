# Professional Diagrams V4

This update replaces the generic/auto-layout appearance of the SRS diagrams with a deterministic professional vector layout system while preserving the international SRS profile and Builder Handoff V2.

## What changed

### Canonical renderer for all 11 diagrams
All customer-visible SVG/PDF diagrams now use the native ReportLab vector renderer. Mermaid remains an editable source/fallback only. LLM output no longer controls the visible layout of System Context, Component or Deployment diagrams.

### Shared visual system
- white canvas (no transparent SVG over grey viewer backgrounds)
- consistent typography, line weights, spacing rhythm and muted engineering-document palette
- larger readable labels with controlled wrapping
- orthogonal routing where it improves readability
- bounded node counts and whitespace suitable for A4 and web preview widths
- centered PDF diagram cards with notation legends kept on the same page

### Use Case Diagram
- UML actor stick figures and use-case ovals
- solid association lines without arrowheads
- actor positions follow the use cases they actually own
- global product features are no longer incorrectly attached to every actor
- system boundary and title use a clean document hierarchy

### Sequence Diagram
- actor is selected from the workflow text instead of blindly using the first role
- API endpoints are matched only when both route vocabulary and HTTP method semantics fit
- unrelated API endpoints are not repeated for arbitrary workflow steps
- database access is shown only where a relevant data entity can be matched
- synchronous calls / dashed returns / lifelines / activation remain standards-oriented

### ER Diagram
- relation-aware layered layout
- PK/FK markers plus field types
- relationships are recovered from explicit field references when the top-level relationship list is incomplete
- Crow's Foot cardinality endpoints are preserved
- connector routes are drawn behind entities and avoid field text

### Activity Diagram
- larger action nodes and readable workflow title
- decisions are drawn only for explicit guarded alternatives
- no invented branch behavior

### Class & Object Diagram
- class boxes no longer overlap the object snapshot
- attributes use a consistent UML-like compartment treatment
- association multiplicities are shown without inventing navigability arrows
- object instance is visually separated and underlined

### State Machine Diagram
If the SRS does not define legal state-to-state transitions, V4 marks the diagram as Not Applicable. It does not fabricate a fake lifecycle just to fill the appendix.

### DFD
- only workflow-relevant data stores are included when a semantic match exists
- unrelated Users/Roles tables are not dragged into a purchase/catalogue DFD
- external entities, processes and stores use a clean three-column composition
- data-flow routing is orthogonal and labelled

### BPMN
- larger pool/lane layout
- participant lanes are derived from role names explicitly present in workflow steps
- gateways are used only for explicit branch semantics
- sequence flow remains inside the pool; no fake message flow is introduced

### System Context / Component / Deployment
These supplemental views are now native/deterministic too.
- Context: actors left, system center, integrations right, persistent data below
- Component: Presentation / Application-Domain / Data-External bands with UML component glyphs
- Deployment: UML-style device/execution-environment nodes with communication paths

## PDF integration
The PDF generator now reserves notation-legend space before scaling the diagram. This prevents an otherwise complete diagram from leaving two notation bullets alone on a nearly blank next page.

## Compatibility
No external diagram engine or new runtime dependency was introduced. Existing Mermaid sources, diagram artifact keys, the international SRS profile, routes, and Builder Handoff V2 remain compatible.
