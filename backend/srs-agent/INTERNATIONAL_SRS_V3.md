# SRS Agent — International SRS & Standards Diagram Upgrade V3

## What changed

This version keeps the Builder Handoff V2 update and upgrades the SRS/document/diagram layer.

### Requirements document profile

- Published requirements-engineering baseline: **ISO/IEC/IEEE 29148:2018**.
- Adds `standards_profile`, `document_control`, revision history, verification method, source/provenance and conservative requirement-quality review metadata.
- PDF cover/footer/reference section no longer claims IEEE 830. It states ISO/IEC/IEEE 29148:2018 alignment and explicitly avoids claiming third-party certification.
- Functional and non-functional requirement tables include verification methods.
- Traceability matrix now carries source/design/data/verification/test evidence.
- Old stored SRS documents receive the profile at PDF render time; customizations re-apply it after edits.

### Canonical standards diagram pack

Eight business-analysis diagrams are deterministic and rendered natively as vector SVG/PDF graphics:

1. Use Case Diagram — OMG UML 2.5.1 notation
2. Sequence Diagram — OMG UML 2.5.1 notation
3. Entity-Relationship Diagram — Crow's Foot notation
4. Activity Diagram — OMG UML 2.5.1 notation
5. Class & Object Diagram — OMG UML 2.5.1 notation
6. State Machine Diagram — OMG UML 2.5.1 notation
7. Data Flow Diagram — conventional Yourdon/DeMarco-style notation
8. BPMN Process Diagram — OMG BPMN 2.0.2 core notation

Three existing engineering views remain as supplemental diagrams: System Context, Component and Deployment.

### Anti-hallucination rules

- Use-case `include`/`extend` links are not invented.
- Activity/BPMN decision gateways are created only when explicit alternative/guard semantics are present.
- State-machine transitions are drawn only when the SRS explicitly states a legal `from X to Y` or `X -> Y` transition. Otherwise the diagram says that no legal transition model is specified.
- BPMN lane ownership is assigned to a role only when the workflow step explicitly names that role; otherwise it stays in a neutral process lane.
- ERD maximum cardinality follows the relationship model; optionality is not fabricated when the SRS does not support it.
- Standard diagrams are not replaced by LLM Mermaid output. The LLM may enrich only the supplemental architecture views.

### Rendering

- `.mmd` sources are still written for portability/editability.
- The eight standards diagrams use **native SVG as the canonical web/print rendering**.
- PDF falls back to the same native ReportLab drawing if no external renderer is installed.
- Each standards diagram includes a notation legend in the SRS PDF.

## Main changed files

- `srs_agent/app/generators/standards.py` (new)
- `srs_agent/app/generators/diagrams.py`
- `srs_agent/app/generators/diagram_draw.py`
- `srs_agent/app/generators/pdf.py`
- `srs_agent/app/agents/diagram_generator.py`
- `srs_agent/app/agents/srs_generator.py`
- `srs_agent/app/agents/customization.py`
- `srs_agent/app/agents/pdf_generator.py`
- `srs_agent/app/schemas/srs.py`

Builder Handoff V2 remains present in `srs_agent/app/generators/builder_brief.py` and the updated SRS router.

Professional diagram renderer: V4 (native deterministic layout for all 11 diagrams).
