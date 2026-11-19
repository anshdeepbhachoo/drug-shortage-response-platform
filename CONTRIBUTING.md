# Contributing to the shortage response platform

Thank you for helping improve the platform. Keep changes small, reviewable, and respectful of teammates' work.

## Branches

| Branch | Role |
|---|---|
| `main` | Stable baseline. Do not open pull requests against it unless the team explicitly agrees. |
| `dev` | Active integration branch. Branch from here and open pull requests into it. |
| `feat/*` | New features |
| `fix/*` | Bug fixes |
| `docs/*` | Documentation only |
| `chore/*` | Repository maintenance and CI |

Do not delete, rename, or force-push shared branches. Leave unfinished teammate branches alone.

## Pull requests

1. Create your branch from the latest `dev`.
2. Keep the pull request focused on one concern.
3. Open the pull request against `dev`, not `main`.
4. Fill in the pull request template.
5. Do not change unrelated application areas.

## Before you open a PR

- Run the checks that apply to your change:
  - Python: `.venv/bin/python -m pytest -q` (or Windows equivalent)
  - Frontend: `cd apps/web && npm test && npm run build` (vitest, then type-check and build)
- Prefer conventional commit messages (`feat:`, `fix:`, `docs:`, `chore:`).
- Never commit secrets, `.env` files, API keys, or credentials.
- Do not rewrite shared Git history.

## Review

- Review your own diff before requesting review.
- Confirm existing tests and dependencies were not changed unless the PR is specifically about them.
- Wait for feedback before merging unfinished work owned by someone else.
