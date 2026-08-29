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
