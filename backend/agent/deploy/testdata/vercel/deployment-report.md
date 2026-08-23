# Deployment Readiness Report

- Project: `corner-shop`
- Primary service: `corner-shop`
- Detected root: `.`
- Framework: Next.js `14.2.3`
- Target: Vercel production deployment
- Database: MongoDB Atlas URI set as a Vercel production environment variable
- Readiness: **45/100** (review phase)
- Required environment variables: MONGODB_URI, NEXT_PUBLIC_SITE_NAME, STRIPE_SECRET_KEY

## Risks

- No model risks reported.

## Recommendations

- Use the generated review checks.

## Security invariants

- Runtime secret values are never included in this report or generated artifacts.
- The MongoDB URI is written to Vercel, not to GitHub.
- **A Vercel deploy token is stored in this repository's GitHub Actions secrets.** Vercel has no OIDC equivalent for deploying, so unlike the AWS target this is a long-lived credential: anyone who can push a workflow to this repository can use it. Scope it to a team and rotate it if the repository changes hands.
- The token is passed to GitHub over stdin and is never written to disk by the agent.

