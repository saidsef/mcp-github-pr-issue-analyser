---
description: Create annotated git tags and publish GitHub releases following semantic versioning
---

# Release Management

Tag a commit and publish a GitHub release with generated or custom release notes.

## Prerequisites

- `repo_owner` and `repo_name` for the target repository
- The target branch/commit must be in a releasable state (CI passing, PRs merged)
- GitHub token with `repo` write access (requires `contents: write` permission)

## Workflow

1. **Get the latest SHA** — call `get_latest_sha` to retrieve the HEAD commit of the default branch
2. **Create a tag** — call `create_tag` with a semantic version string and descriptive message
3. **Publish the release** — call `create_release` referencing the new tag

## Tool Parameters

### `get_latest_sha`

| Parameter | Type | Description |
|---|---|---|
| `repo_owner` | str | GitHub organisation or username |
| `repo_name` | str | Repository name |

Returns: SHA string of the HEAD commit on the default branch.

### `create_tag`

| Parameter | Type | Description |
|---|---|---|
| `repo_owner` | str | GitHub organisation or username |
| `repo_name` | str | Repository name |
| `tag_name` | str | Tag name (e.g. `v1.2.3`) |
| `message` | str | Annotated tag message describing the release |

### `create_release`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | — | GitHub organisation or username |
| `repo_name` | str | — | Repository name |
| `tag_name` | str | — | Existing tag to release from |
| `release_name` | str | — | Human-readable release title |
| `body` | str | — | Release notes (Markdown) |
| `draft` | bool | `False` | Publish as draft (not publicly visible) |
| `prerelease` | bool | `False` | Mark as pre-release (alpha/beta/rc) |
| `generate_release_notes` | bool | `True` | Auto-generate notes from merged PRs |
| `make_latest` | str | `"true"` | Mark as the latest release |

## Semantic Versioning Guide

Format: `vMAJOR.MINOR.PATCH` (e.g. `v2.1.0`)

| Part | Increment when |
|---|---|
| MAJOR | Breaking changes to the public API |
| MINOR | New backwards-compatible features |
| PATCH | Backwards-compatible bug fixes |

Pre-release suffixes: `v1.0.0-alpha.1`, `v1.0.0-beta.2`, `v1.0.0-rc.1`

## Best Practices

- Always follow semver — never re-use or delete a published tag
- Set `draft=True` first to preview the release before publishing
- Set `generate_release_notes=True` unless you need full manual control over the notes
- Set `prerelease=True` for alpha/beta/rc versions
- Tag messages should summarise the scope of the release (e.g. "Release v1.2.0: add caching layer and improve error handling")
- Ensure all target PRs are merged before tagging
