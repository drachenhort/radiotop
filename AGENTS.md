# Git, Versioning and Changelog Policy

## Git workflow

Aider is responsible for maintaining the Git history for changes it makes.

Before making substantial changes:

1. Check `git status`.
2. Check the current branch.
3. Review recent commits when necessary.
4. Never overwrite or discard existing user changes without explicit permission.

After completing a coherent change:

1. Review the diff.
2. Run the relevant tests.
3. Update documentation if required.
4. Update the changelog.
5. Bump the version when appropriate.
6. Create a Git commit.
7. Push the commit to the configured remote.

Do not leave completed work as an uncommitted working-tree change unless explicitly requested.

## Commits

Create a separate commit for each coherent logical change.

Use Conventional Commits:

- `feat:` for new functionality
- `fix:` for bug fixes
- `docs:` for documentation
- `refactor:` for refactoring
- `perf:` for performance improvements
- `test:` for tests
- `build:` for build/dependency changes
- `chore:` for maintenance

Commit messages should clearly describe the change.

Never use `git commit --amend` on a commit that has already been pushed unless explicitly instructed.

Never rewrite published history.

## Versioning

Follow Semantic Versioning:

MAJOR.MINOR.PATCH

Use:

- PATCH for backwards-compatible bug fixes
- MINOR for backwards-compatible new functionality
- MAJOR for breaking changes

Do not bump the version for trivial internal changes unless the project convention requires it.

Before bumping a version, determine where the project stores its authoritative version number.

Keep all version references synchronized.

## Changelog

Maintain `CHANGELOG.md`.

Every user-visible change must have an appropriate changelog entry.

Use the existing changelog format if one exists.

Group changes under:

- Added
- Changed
- Fixed
- Removed
- Security

Do not rewrite historical changelog entries unnecessarily.

When releasing a new version, create a versioned changelog section with the release date.

## Releases

When a version bump represents a release:

1. Update the version.
2. Update `CHANGELOG.md`.
3. Run tests.
4. Review the final diff.
5. Commit the release.
6. Create a Git tag matching the version, e.g. `v1.2.3`.
7. Push the commit and tag.

Never create a release tag for a version that has failing tests.

## Pushes

Push completed commits to the configured remote.

Before pushing:

- Verify the branch.
- Verify the remote.
- Verify the working tree.
- Review the commits that will be pushed.

Never force-push unless explicitly instructed.

Never use `git push --force` or `git push --force-with-lease` automatically.

## Recovery and rollback

Git history must remain easy to recover.

Before risky operations:

- Create a checkpoint commit when appropriate.
- Do not use destructive commands such as `git reset --hard`, `git clean -fd`, or force pushes without explicit approval.

If a change causes problems:

1. Identify the problematic commit.
2. Prefer `git revert` for already-pushed commits.
3. Do not rewrite published history.
4. Preserve the existing history so the failure can be diagnosed and recovered.

When uncertain whether a Git operation is destructive, stop and ask for confirmation.

## Final verification

Before considering a task complete:

- `git status`
- tests
- `git diff`
- `git log`
- version consistency
- changelog consistency

The goal is that every completed piece of work is committed, versioned appropriately, documented, and recoverable.

