---
description: Tag a commit and publish a GitHub release, following semantic versioning
---

# Release Management

Create a tag on the default branch and publish a GitHub release against it.

## Prerequisites

- `repo_owner` and `repo_name` for the target repository
- The default branch must be releasable: CI passing, every intended PR merged
- GitHub token with `contents: write` access

## Workflow

1. **Check the target** - call `get_latest_sha` to see which commit will be tagged
2. **Create the tag** - call `create_tag` with a semantic version string
3. **Publish the release** - call `create_release` against that tag

## Tool Parameters

### `get_latest_sha`

| Parameter | Type | Description |
|---|---|---|
| `repo_owner` | str | GitHub organisation or username |
| `repo_name` | str | Repository name |

Returns the SHA of the newest commit on the default branch, or `None` if the
repository has no commits.

### `create_tag`

| Parameter | Type | Description |
|---|---|---|
| `repo_owner` | str | GitHub organisation or username |
| `repo_name` | str | Repository name |
| `tag_name` | str | Tag name, e.g. `v1.2.3` |
| `message` | str | Description of the release |

Two limits to work within:

- The commit is not selectable. `create_tag` resolves the default branch HEAD itself, so it always tags the newest commit at the moment of the call. There is no way to tag an older commit or a different branch through this tool
- The tag is a lightweight ref, not an annotated tag object, so `message` is not stored on the tag. Put the release prose in the `body` of `create_release`, which is what readers actually see

Calling `get_latest_sha` first does not pin the commit, it only shows you what
`create_tag` is about to pick up. Re-read it if time has passed or a merge may
have landed.

Fails with a not-found error if the repository has no commits.

### `create_release`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `tag_name` | str | - | Existing tag to release from |
| `release_name` | str | - | Release title |
| `body` | str | - | Release notes in Markdown |
| `draft` | bool | `False` | Create as a draft, not publicly visible |
| `prerelease` | bool | `False` | Mark as a pre-release |
| `generate_release_notes` | bool | `True` | Append GitHub's auto-generated notes to `body` |
| `make_latest` | str | `"true"` | One of `"true"`, `"false"`, `"legacy"`, as strings not booleans |

Returns `id`, `tag_name`, `name`, `html_url`, `draft`, `prerelease`, `body`.

`generate_release_notes=True` appends GitHub's merged-PR list to your `body`
rather than replacing it, so a hand-written `What Changed` section will be
duplicated. Set it to `False` when you supply that section yourself.

## Semantic Versioning Guide

Format `vMAJOR.MINOR.PATCH`, e.g. `v2.1.0`.

| Part | Increment when |
|---|---|
| MAJOR | Breaking changes to the public API |
| MINOR | New backwards-compatible features |
| PATCH | Backwards-compatible bug fixes |

Pre-release suffixes: `v1.0.0-alpha.1`, `v1.0.0-beta.2`, `v1.0.0-rc.1`.

## Release Body Format

```markdown
## v{MAJOR}.{MINOR}.{PATCH} - {YYYY-MM-DD}

### What's Included

- **Feature or fix label** - brief description
- **Another change** - brief description

### Breaking Changes (from v{PREV_MAJOR}.x)

1. **Change title** - description and migration path
2. **Another breaking change** - description

### New Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `VAR_NAME` | Yes/No | What it controls |

### What Changed

- type(scope): commit message (SHORT_SHA) by @author

**Full Changelog**: https://github.com/{owner}/{repo}/compare/v{PREV}...v{NEW}
```

Rules:

- Single dashes only, never em-dashes
- Date is `YYYY-MM-DD` and is today's date
- Drop `Breaking Changes` and `New Environment Variables` when they do not apply
- `What Changed` lists every commit as `<type>(<scope>): <prose summary>`, the same form used for issue and PR titles. `<type>` is one of `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`, `ci`, `build`. `<scope>` is the lowercase area touched such as `auth`, `deps`, `cache`, and the summary is prose, not a kebab-case slug
- Short SHA is the first 7 characters of the commit hash
- `Full Changelog` compares the previous tag to the new one
- Write this section yourself only with `generate_release_notes=False`, otherwise GitHub appends its own copy

## Best Practices

- Follow semver, and never re-use or delete a published tag
- Confirm every intended PR is merged before tagging, since the tag follows the default branch HEAD
- Publish with `draft=True` first to preview, then flip it once the notes read correctly
- Set `prerelease=True` for alpha, beta and rc versions, which also keeps them off the latest-release badge
- Put the release prose in `create_release`'s `body`, because the `create_tag` message does not survive
- Pick one source for the commit list: either `generate_release_notes=True` or a hand-written `What Changed`, not both
