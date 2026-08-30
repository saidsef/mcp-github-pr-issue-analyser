---
description: Put issues and pull requests on a GitHub project board, set their fields, read what is on a board, and take items off it
---

# Project Boards

Read and change GitHub Projects (v2) boards: what is on one, what each card's
fields say, and where an issue sits.

## Prerequisites

- `project_owner` and `project_number`, both readable from the board's URL:
  `https://github.com/users/OWNER/projects/NUMBER` or
  `https://github.com/orgs/OWNER/projects/NUMBER`
- `repo_owner`, `repo_name` and `issue_number` for the issue or pull request
- A token that reaches Projects. This is a separate grant from `repo`:
  a classic token needs `read:project` to read and `project` to write, a
  fine-grained one needs Projects read and write

The board's owner is not always the repository's owner. An organisation board
holding issues from several repositories is the common case, so read
`project_owner` off the board URL rather than assuming it matches `repo_owner`.

## Workflow

### Filing an issue and placing it

1. Call `create_issue` as usual
2. Call `get_project_fields` to see which fields the board has and what each
   single-select one accepts
3. Call `set_project_field` with the field and option names. It puts the issue
   on the board first if it is not already there, so `add_to_project` is only
   needed when no field is being set

### Triaging a backlog

1. Call `list_project_items` to read every card with its field values
2. Group by the `fields` map, for example by `Status`, to see what is where
3. Call `set_project_field` per issue to move it

### Taking something off

1. Call `remove_from_project`
2. The issue stays open and untouched. Only the card goes, and the field values
   it held go with it
3. **Ask the user in chat before removing a card, since the field values cannot
   be recovered**

## Tool Parameters

### `get_project_fields`

| Parameter | Type | Description |
|---|---|---|
| `project_owner` | str | User or organisation that owns the board |
| `project_number` | int | Project number, as it appears in the board's URL |

Returns `project_number`, `title`, `url` and `fields`. Each field carries its
`id`, `name`, `data_type` and `options`. `options` is empty for anything that is
not a single select, which is what tells you `set_project_field` will refuse it.

Call this before `set_project_field` rather than guessing an option name. Boards
rename `Status` options freely, and `Todo`, `To do` and `Backlog` are all in use.

### `list_project_items`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project_owner` | str | - | User or organisation that owns the board |
| `project_number` | int | - | Project number |
| `per_page` | int | `50` | Items per page, 1 to 100 |
| `after` | str \| None | `None` | `next_cursor` from a previous call |

Returns `total`, `title` and `items`. Each item carries `item_id`, `type`,
`number`, `title`, `state`, `url`, `repository` and a `fields` map keyed by
field name.

`next_cursor` is a cursor when there is another page and `None` when there is
not, so pass it back until it comes back `None`.

Draft issues on the board have a `title` but no `number`, `url` or `repository`,
because they are notes on the board rather than issues in a repository.

### `add_to_project`

| Parameter | Type | Description |
|---|---|---|
| `project_owner` | str | User or organisation that owns the board |
| `project_number` | int | Project number |
| `repo_owner` | str | Owner of the repository the issue is in |
| `repo_name` | str | Repository name |
| `issue_number` | int | Issue or pull request to place |

Returns `item_id`, `project_number`, `project_title`, `issue_number` and `url`.

Works on a pull request as well as an issue, without being told which it is.

Adding something already on the board returns the card it already has rather
than making a second one, so this is safe to retry.

### `set_project_field`

| Parameter | Type | Description |
|---|---|---|
| `project_owner` | str | User or organisation that owns the board |
| `project_number` | int | Project number |
| `repo_owner` | str | Owner of the repository the issue is in |
| `repo_name` | str | Repository name |
| `issue_number` | int | Issue or pull request whose card to change |
| `field` | str | Single-select field to set, such as `Status` |
| `option` | str | Option to set it to, such as `In Progress` |

Returns `item_id`, `project_number`, `issue_number`, `field` and `option`.

Field and option names are matched without regard to case, so `status` and
`Status` both work. Everything else must match exactly.

Single-select fields only. Text, number, date and iteration fields are rejected
with a message naming the type, because an option name means nothing to them.

An issue not yet on the board is added first, since a field value has nowhere to
live otherwise.

### `remove_from_project`

| Parameter | Type | Description |
|---|---|---|
| `project_owner` | str | User or organisation that owns the board |
| `project_number` | int | Project number |
| `repo_owner` | str | Owner of the repository the issue is in |
| `repo_name` | str | Repository name |
| `issue_number` | int | Issue or pull request to take off the board |

Returns `status`, `item_id`, `project_number` and `issue_number`.

Destructive and not reversible. The issue is untouched and stays open. The card
goes, and every field value on it goes with it. Adding the issue back gives a
fresh card with no values set.

An issue that is not on this board is refused rather than added so it can be
removed. An item on a different board does not count.

## Errors

| Message | Means |
|---|---|
| `No project #N for 'owner'` | Wrong owner or number, or the token cannot see Projects. The two are indistinguishable from the API |
| `The token is missing a scope this query needs` | The token reaches the repository but not Projects. Grant `read:project` or `project` |
| `No field named 'X' on this project` | The message lists the fields that are there. Pick one of those |
| `No option named 'X' on field 'Y'` | The message lists the options that field accepts |
| `Field 'X' is a TEXT field` | Not a single select. `set_project_field` cannot set it |
| `#N in owner/repo is not on project #M` | Call `add_to_project` first, or check the project number |

## Best Practices

- Read the board with `get_project_fields` before writing to it, so the option
  names come from the board rather than from a guess
- Take `project_owner` from the board URL, not from the repository
- Use `set_project_field` alone to file and place an issue in one step, since it
  adds the card itself
- Confirm with the user in chat before `remove_from_project`, since the field
  values go with the card
- Page `list_project_items` to the end before reporting a count, since a board
  larger than `per_page` otherwise reads as smaller than it is
