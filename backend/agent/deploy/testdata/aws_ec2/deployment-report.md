# Deployment Readiness Report

- Project: `corner-shop`
- Primary service: `corner-shop`
- Detected root: `.`
- Framework: Next.js `14.2.3`
- Target: AWS EC2 (t3.micro) behind nginx, released from S3 via SSM
- Database: MongoDB Atlas URI written directly to AWS Secrets Manager
- Readiness: **45/100** (review phase)
- Required environment variables: MONGODB_URI, NEXT_PUBLIC_SITE_NAME, STRIPE_SECRET_KEY

## Risks

- No model risks reported.

## Recommendations

- Use the generated review checks.

## Security invariants

- Runtime secret values are never included in this report or generated artifacts.
- No cloud access keys or provider tokens are written to GitHub; Actions authenticates through OIDC.
- Provider credentials are kept only in the in-memory credential vault.
- The instance security group exposes port 80 only; the Next.js process listens on loopback.

