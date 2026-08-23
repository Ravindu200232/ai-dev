# Deployment Readiness Report

- Project: `<%.Project%>`
- Primary service: `<%.Service%>`
- Detected root: `<%.Root%>`
- Framework: Next.js `<%.Version%>`
- Target: <%.TargetSummary%>
- Database: <%.DatabaseNote%>
- Readiness: **<%.Score%>/100** (review phase)
- Required environment variables: <%.Environment%>

## Risks

<%range .Risks%>- <%.%>
<%end%>
## Recommendations

<%range .Recommendations%>- <%.%>
<%end%>
## Security invariants

- Runtime secret values are never included in this report or generated artifacts.
<%range .SecurityNotes%>- <%.%>
<%end%>
