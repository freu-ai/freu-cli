---
name: GitHub
description: >
  Interact with GitHub repositories, issues, and other resources. Use this skill for actions like starring repositories, opening issues, and navigating project pages.
version: 1.0.0
---

# GitHub — Agent Skill

Interact with GitHub repositories, issues, and other resources. Use this skill for actions like starring repositories, opening issues, and navigating project pages.

## StarRepository

Star a specific GitHub repository when given its URL.

### CLI
freu-cli run GitHub StarRepository --repository-url <repository_url>

### Arguments
- **repository_url** → The full URL of the GitHub repository to star (e.g., https://github.com/owner/repo).

### Outputs
-

## FindRepoWebsite

Open a specific GitHub repository and retrieve the URL of its linked project website from the repo page.

### CLI
freu-cli run GitHub FindRepoWebsite --repo-url <repo_url>

### Arguments
- **repo_url** → The full URL of the GitHub repository whose project website should be found.

### Outputs
- **project_website_url** → The URL of the project website linked from the GitHub repository page.

## FindMostCommentedIssue

Open a GitHub repository's issues list, sort by total comments, and read the title of the most commented issue.

### CLI
freu-cli run GitHub FindMostCommentedIssue --repository-url <repository_url>

### Arguments
- **repository_url** → The URL of the GitHub repository whose most commented issue should be found (e.g., https://github.com/owner/repo).

### Outputs
- **most_commented_issue_title** → Title of the most commented issue in the specified GitHub repository.
