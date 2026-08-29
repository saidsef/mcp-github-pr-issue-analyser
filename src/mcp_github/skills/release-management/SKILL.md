---
description: Tag a commit, publish a GitHub release, and read, correct or withdraw the tags and releases already there
---

# Release Management

Create a tag on the default branch and publish a GitHub release against it,
then read, correct or withdraw what has already been published.

## Prerequisites

- `repo_owner` and `repo_name` for the target repository
- The default branch must be releasable: CI passing, every intended PR merged
- GitHub token with `contents: write` access

## Workflow

### Publishing

1. **Check the target** - call `get_latest_sha` to see which commit will be tagged
2. **Check what is already out** - call `get_release` with no tag for the current latest, or `list_releases` for the history
3. **Create the tag** - call `create_tag` with a semantic version string
4. **Publish the release** - call `create_release` against that tag

### Correcting a release

1. Call `update_release` with only the fields that are wrong
2. Publishing again over the same tag also works: `create_release` updates a tag that already has a release rather than failing

### Withdrawing a release

1. Call `delete_release`, which leaves the tag in place
2. Pass `delete_tag=True` only when the tag itself was a mistake
3. **Deleting a published release breaks any link to it. Ask the user in chat and get an explicit yes first**

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
| `message` | str | Tag message. Omit for a lightweight tag |
| `sha` | str | Commit to tag. Omit to tag the newest commit on the default branch |

Pass `sha` to cut a release from a known commit. Without it the tool resolves
the default branch HEAD itself, and what that points at can change between
reading it and tagging it.

With a `message` you get an annotated tag, which is a real object holding the
message and the tagger. Without one you get a lightweight ref straight to the
commit. The release prose still belongs in the `body` of `create_release`,
since that is what readers see on the releases page.

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

A tag that already carries a release is updated instead of rejected, so a retry
after a half-finished release recovers rather than erroring.

### `list_releases`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `per_page` | int | `30` | Results per page, 1 to 100 |
| `page` | int | `1` | Page number |

Returns `total` and `releases`, newest first, each trimmed to the same fields
`create_release` returns. Drafts appear only for a token that can see them.

### `get_release`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `tag_name` | str \| None | `None` | Tag to fetch. Omit for the latest published release |

The latest release is the newest non-draft, non-prerelease one, which is not
always the highest version number. Pass `tag_name` when you mean a specific one.

### `update_release`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `tag_name` | str | - | Tag of the release to change |
| `name` | str \| None | `None` | Replacement title |
| `body` | str \| None | `None` | Replacement notes in Markdown |
| `draft` | bool \| None | `None` | Publish a draft with `False`, or pull one back with `True` |
| `prerelease` | bool \| None | `None` | Mark or unmark as a pre-release |
| `make_latest` | str \| None | `None` | One of `"true"`, `"false"`, `"legacy"`, as strings |

Only the fields you pass are sent, so correcting a title leaves the notes alone.
The notes replace rather than append, so read the release first if you are
adding to them. A call supplying nothing to change is rejected.

### `list_tags`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `per_page` | int | `30` | Results per page, 1 to 100 |
| `page` | int | `1` | Page number |

Returns `total` and `tags`, each a `name` and the `sha` it points at.

### `delete_release`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `tag_name` | str | - | Tag of the release to delete |
| `delete_tag` | bool | `False` | Also remove the tag it was published from |

Destructive and not reversible. The tag survives by default, so the commit stays
reachable and the release can be published again.

### `delete_tag`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `repo_owner` | str | - | GitHub organisation or username |
| `repo_name` | str | - | Repository name |
| `tag_name` | str | - | Tag to delete |
| `force` | bool | `False` | Delete even though a release was published from it |

Destructive and not reversible. A tag a release points at is refused unless
`force=True`, because removing it leaves the release naming code nobody can
fetch. Delete the release first instead.

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

- Follow semver, and treat a published tag as permanent. Correct the release with `update_release` rather than deleting and re-cutting it
- Confirm with the user in chat before `delete_release` or `delete_tag`, since neither can be undone
- Confirm every intended PR is merged before tagging, since the tag follows the default branch HEAD
- Publish with `draft=True` first to preview, then flip it once the notes read correctly
- Set `prerelease=True` for alpha, beta and rc versions, which also keeps them off the latest-release badge
- Put the release prose in `create_release`'s `body`, because the `create_tag` message does not survive
- Pick one source for the commit list: either `generate_release_notes=True` or a hand-written `What Changed`, not both
