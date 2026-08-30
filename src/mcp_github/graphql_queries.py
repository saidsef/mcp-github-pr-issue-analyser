#!/usr/bin/env python3

# /*
#  * Copyright Said Sef
#  *
#  * Licensed under the Apache License, Version 2.0 (the "License");
#  * you may not use this file except in compliance with the License.
#  * You may obtain a copy of the License at
#  *
#  *      https://www.apache.org/licenses/LICENSE-2.0
#  *
#  * Unless required by applicable law or agreed to in writing, software
#  * distributed under the License is distributed on an "AS IS" BASIS,
#  * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  * See the License for the specific language governing permissions and
#  * limitations under the License.
#  */

"""GraphQL query definitions for GitHub API v4."""

from __future__ import annotations

# Query to search for a GitHub user by username
SEARCH_USER_QUERY = """
query($username: String!) {
  user(login: $username) {
    login
    name
    email
    company
    location
    bio
    url
    avatarUrl
    createdAt
    updatedAt
    followers {
      totalCount
    }
    following {
      totalCount
    }
    repositories(privacy: PUBLIC, first: 10, orderBy: {field: UPDATED_AT, direction: DESC}) {
      totalCount
      nodes {
        name
        owner {
          login
        }
        description
        url
        updatedAt
      }
    }
    organizations(first: 100) {
      totalCount
      nodes {
        login
        name
        url
      }
    }
  }
}
"""

# Query to fetch issues that will be auto-closed when a PR is merged
PR_LINKED_ISSUES_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      closingIssuesReferences(first: 25) {
        nodes {
          number
          title
          state
          url
          createdAt
          labels(first: 10) {
            nodes {
              name
            }
          }
        }
      }
    }
  }
}
"""

# Query to fetch check runs and commit status for a PR's HEAD commit.
# Paginates check suites via $suitesAfter. Runs-within-suite are returned
# 100 at a time alongside an endCursor and hasNextPage; if a suite has
# more, the caller follows up with CHECK_SUITE_RUNS_QUERY using the
# suite's node id.
PR_STATUS_CHECKS_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $suitesAfter: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      headRef {
        target {
          ... on Commit {
            checkSuites(first: 50, after: $suitesAfter) {
              pageInfo {
                hasNextPage
                endCursor
              }
              nodes {
                id
                app {
                  name
                }
                status
                conclusion
                checkRuns(first: 100) {
                  pageInfo {
                    hasNextPage
                    endCursor
                  }
                  nodes {
                    name
                    status
                    conclusion
                    detailsUrl
                  }
                }
              }
            }
            status {
              state
              contexts {
                context
                state
                description
                targetUrl
              }
            }
          }
        }
      }
    }
  }
}
"""

# Supplemental query: paginate check runs for a single check suite by id.
# Used to drain the runs of a suite whose first page was truncated by
# PR_STATUS_CHECKS_QUERY.
CHECK_SUITE_RUNS_QUERY = """
query($suiteId: ID!, $after: String) {
  node(id: $suiteId) {
    ... on CheckSuite {
      checkRuns(first: 100, after: $after) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          name
          status
          conclusion
          detailsUrl
        }
      }
    }
  }
}
"""

# Query to get user contributions with optional date filtering.
# Only fields the mappers in activity.py actually read are requested -- the
# collection is fetched wide (100 repos x 100 contributions) because org and
# repo filtering has no server-side equivalent on contributionsCollection.
USER_CONTRIBUTIONS_QUERY = """
fragment RepoRef on Repository {
  name
  owner {
    login
  }
}

query($username: String!, $since: DateTime, $until: DateTime) {
  user(login: $username) {
    contributionsCollection(from: $since, to: $until) {
      startedAt
      endedAt
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
      totalPullRequestReviewContributions
      commitContributionsByRepository(maxRepositories: 100) {
        repository {
          ...RepoRef
        }
        contributions(first: 100) {
          nodes {
            occurredAt
            commitCount
            url
          }
        }
      }
      pullRequestContributionsByRepository(maxRepositories: 100) {
        repository {
          ...RepoRef
        }
        contributions(first: 100) {
          nodes {
            occurredAt
            pullRequest {
              number
              title
              state
              url
              createdAt
              merged
            }
          }
        }
      }
      issueContributionsByRepository(maxRepositories: 100) {
        repository {
          ...RepoRef
        }
        contributions(first: 100) {
          nodes {
            occurredAt
            issue {
              number
              title
              state
              url
              createdAt
            }
          }
        }
      }
      pullRequestReviewContributionsByRepository(maxRepositories: 100) {
        repository {
          ...RepoRef
        }
        contributions(first: 100) {
          nodes {
            occurredAt
            pullRequest {
              number
              title
              url
            }
            pullRequestReview {
              state
              url
            }
          }
        }
      }
    }
    repositories(privacy: PUBLIC, first: 100, orderBy: {field: STARGAZERS, direction: DESC}) {
      nodes {
        name
        owner {
          login
        }
        url
        description
        stargazerCount
      }
    }
  }
}
"""

# Draft status is settable through REST only when the pull request is created,
# so moving an existing one either way goes through GraphQL. See #348.
MARK_PR_READY_MUTATION = """
mutation($pullRequestId: ID!) {
  markPullRequestReadyForReview(input: {pullRequestId: $pullRequestId}) {
    pullRequest {
      number
      isDraft
      url
    }
  }
}
"""

CONVERT_PR_TO_DRAFT_MUTATION = """
mutation($pullRequestId: ID!) {
  convertPullRequestToDraft(input: {pullRequestId: $pullRequestId}) {
    pullRequest {
      number
      isDraft
      url
    }
  }
}
"""

# Projects (v2) has no REST surface, and everything on it is addressed by node
# id rather than by owner, repo and number. See #351.
#
# repositoryOwner resolves a login without the caller saying whether it names a
# user or an organisation, which projectV2 on either type alone cannot do.
_PROJECT_FIELDS_FRAGMENT = """
fragment ProjectFields on ProjectV2 {
  id
  number
  title
  url
  fields(first: 50) {
    nodes {
      ... on ProjectV2Field {
        id
        name
        dataType
      }
      ... on ProjectV2IterationField {
        id
        name
        dataType
      }
      ... on ProjectV2SingleSelectField {
        id
        name
        dataType
        options {
          id
          name
        }
      }
    }
  }
}
"""

PROJECT_QUERY = (
    _PROJECT_FIELDS_FRAGMENT
    + """
query($owner: String!, $number: Int!) {
  repositoryOwner(login: $owner) {
    ... on Organization {
      projectV2(number: $number) {
        ...ProjectFields
      }
    }
    ... on User {
      projectV2(number: $number) {
        ...ProjectFields
      }
    }
  }
}
"""
)

# issueOrPullRequest saves the caller saying which of the two a number is.
# projectItems rides along because setting a field or taking a card off the
# board needs the item id, and asking for it separately would cost a round trip.
ISSUE_PROJECT_ITEMS_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    issueOrPullRequest(number: $number) {
      ... on Issue {
        id
        number
        title
        url
        projectItems(first: 20) {
          nodes {
            id
            project {
              id
              number
            }
          }
        }
      }
      ... on PullRequest {
        id
        number
        title
        url
        projectItems(first: 20) {
          nodes {
            id
            project {
              id
              number
            }
          }
        }
      }
    }
  }
}
"""

# Every field value is a different type in a union, so each one the board can
# hold is selected by name. A type not selected here comes back as an empty
# node, which the flattener drops because it carries no field to key on.
PROJECT_ITEMS_QUERY = """
fragment ItemPage on ProjectV2 {
  id
  number
  title
  url
  items(first: $first, after: $after) {
    totalCount
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      id
      type
      content {
        ... on Issue {
          number
          title
          state
          url
          repository {
            nameWithOwner
          }
        }
        ... on PullRequest {
          number
          title
          state
          url
          repository {
            nameWithOwner
          }
        }
        ... on DraftIssue {
          title
        }
      }
      fieldValues(first: 20) {
        nodes {
          ... on ProjectV2ItemFieldTextValue {
            text
            field {
              ... on ProjectV2FieldCommon {
                name
              }
            }
          }
          ... on ProjectV2ItemFieldNumberValue {
            number
            field {
              ... on ProjectV2FieldCommon {
                name
              }
            }
          }
          ... on ProjectV2ItemFieldDateValue {
            date
            field {
              ... on ProjectV2FieldCommon {
                name
              }
            }
          }
          ... on ProjectV2ItemFieldSingleSelectValue {
            name
            field {
              ... on ProjectV2FieldCommon {
                name
              }
            }
          }
          ... on ProjectV2ItemFieldIterationValue {
            title
            field {
              ... on ProjectV2FieldCommon {
                name
              }
            }
          }
        }
      }
    }
  }
}

query($owner: String!, $number: Int!, $first: Int!, $after: String) {
  repositoryOwner(login: $owner) {
    ... on Organization {
      projectV2(number: $number) {
        ...ItemPage
      }
    }
    ... on User {
      projectV2(number: $number) {
        ...ItemPage
      }
    }
  }
}
"""

# GitHub returns the item already there rather than a second one, which is what
# makes adding safe to retry.
ADD_PROJECT_ITEM_MUTATION = """
mutation($projectId: ID!, $contentId: ID!) {
  addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
    item {
      id
    }
  }
}
"""

SET_PROJECT_FIELD_MUTATION = """
mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
  updateProjectV2ItemFieldValue(
    input: {
      projectId: $projectId
      itemId: $itemId
      fieldId: $fieldId
      value: {singleSelectOptionId: $optionId}
    }
  ) {
    projectV2Item {
      id
    }
  }
}
"""

DELETE_PROJECT_ITEM_MUTATION = """
mutation($projectId: ID!, $itemId: ID!) {
  deleteProjectV2Item(input: {projectId: $projectId, itemId: $itemId}) {
    deletedItemId
  }
}
"""
