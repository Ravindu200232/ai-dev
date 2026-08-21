# SRS maintenance map

Keep public imports stable and place new logic in the focused module that owns it.

- `agents/plan_generator.py` — planning facade.
- `knowledge/plan_srs.py` — SRS structure and access rules.
- `knowledge/topics.py` — topic helpers and catalog data.
- `knowledge/domains.py` — domain types and classification.
- `generators/builder_brief.py` — builder handoff.
- `generators/diagrams.py` — diagram source and rendering.

The builder handoff must preserve approved requirements, routes, roles, account policy, APIs, data relationships and acceptance flows. Public registration must never create privileged roles. Avoid arbitrary truncation of approved scope.
