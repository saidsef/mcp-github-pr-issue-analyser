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


def _owner_query(variables: str, selection: str) -> str:
    """Wrap a selection in the repositoryOwner shell. A board's owner may be a
    person or an organisation, and GraphQL needs the selection under both."""
    return f"""
query({variables}) {{
  repositoryOwner(login: $owner) {{
    ... on Organization {{
      {selection}
    }}
    ... on User {{
      {selection}
    }}
  }}
}}
"""


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

PROJECT_QUERY = _PROJECT_FIELDS_FRAGMENT + _owner_query(
    "$owner: String!, $number: Int!", "projectV2(number: $number) { ...ProjectFields }"
)

_PROJECT_PAGE_FRAGMENT = """
fragment ProjectPage on ProjectV2Connection {
  totalCount
  pageInfo {
    hasNextPage
    endCursor
  }
  nodes {
    number
    title
    closed
    url
  }
}
"""

PROJECTS_QUERY = _PROJECT_PAGE_FRAGMENT + _owner_query(
    "$owner: String!, $first: Int!, $after: String", "projectsV2(first: $first, after: $after) { ...ProjectPage }"
)

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

_ITEM_PAGE_FRAGMENT = """
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
"""

PROJECT_ITEMS_QUERY = _ITEM_PAGE_FRAGMENT + _owner_query(
    "$owner: String!, $number: Int!, $first: Int!, $after: String", "projectV2(number: $number) { ...ItemPage }"
)

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
