# Pull Request Workflow

This document outlines the pull request (PR) workflow for the crypto-agent project.

## Overview

All changes to the main branch must go through the PR review process. Direct pushes to main are prohibited.

## Branch Protection Rules

The `main` branch has the following protection rules:

- **Require a pull request before merging**: yes
- **Required approvals**: 0 — solo maintainer; GitHub forbids self-approval. Raise to 1 when a second collaborator exists.
- **Dismiss stale reviews**: enabled (takes effect once approvals are required)
- **Required status checks**: lint, test, docker-test. Branches are NOT required to be up to date before merging.
- **Enforce for admins**: yes — admins cannot bypass
- **Block force pushes**: yes
- **Block deletions**: yes

## Workflow

### 1. Create a Feature Branch

```bash
# From main, create a new branch
git checkout main
git pull origin main
git checkout -b feat/your-feature-name
```

### 2. Make Changes

Make your changes following the project's coding standards. See `CLAUDE.md` for guidelines.

### 3. Commit Changes

Use conventional commit format:

```bash
git add <specific-files>
git commit -m "feat: add new trading strategy"
```

Commit types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `refactor`: Code refactoring
- `test`: Test additions/changes
- `chore`: Maintenance tasks

### 4. Push Branch

```bash
git push -u origin feat/your-feature-name
```

### 5. Create Pull Request

Using GitHub CLI:

```bash
gh pr create --title "feat: add new trading strategy" --body "Description of changes"
```

Or via GitHub web interface.

### 6. Code Review

- Request review from at least 1 team member
- Address feedback promptly
- Re-request review after changes

### 7. Merge

Once approved and CI passes:

```bash
gh pr merge --squash
```

**Merging does not authorize production deployment.** `.github/workflows/deploy.yml`
runs only via manual `workflow_dispatch` with a required `deploy_sha` that must
equal the current `origin/main` SHA. See `AGENTS.md` for the exact invocation.

Or use the GitHub web interface.

## Review Guidelines

### For Authors

- Keep PRs focused and small
- Write clear PR descriptions
- Link related issues
- Respond to feedback constructively

### For Reviewers

- Review within 24 hours
- Be constructive in feedback
- Approve when ready, request changes when needed
- Check: code quality, tests, documentation

## Emergency Procedures

If you need to bypass protection rules (emergency only):

1. Contact a repository admin
2. Document the reason
3. Create an issue to address the root cause

## Related Documentation

- `CLAUDE.md` - Agent coordination and coding standards
- `CONTRIBUTING.md` - Contribution guidelines
- `README.md` - Project overview
