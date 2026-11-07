# Issue tracker: GitHub

Issues and specs live in GitHub Issues for `dapih/Calibre-Web-NextGen`.
Use the `gh` CLI. For every issue, label, PR, or list command below, add
`--repo dapih/Calibre-Web-NextGen` so a fork cannot default to its upstream repository.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body-file <body.md>`. Write multiline text to a UTF-8 file and pass its path; preserve real newlines.
- **Read an issue**: `gh issue view <number> --json number,title,body,labels,comments`.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments,assignees`. Add label and state filters as needed, and paginate with `gh api` when a complete inventory is required.
- **Link a sub-issue**: `gh issue create --parent <parent> ...`, or `gh issue edit <parent> --add-sub-issue <child>` (gh 2.94+). On older versions, use `gh api --method POST repos/dapih/Calibre-Web-NextGen/issues/<parent>/sub_issues -F sub_issue_id=<child-database-id>`. If sub-issues are unavailable, add `Part of #<parent>` to the child body and a task-list link in the parent.
- **Blocking**: use native issue dependencies: `gh issue create --blocked-by <n>,<n> ...` or `gh issue edit <child> --add-blocked-by <blocker>` (gh 2.94+). On older versions, use `gh api --method POST repos/dapih/Calibre-Web-NextGen/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-database-id>`. Obtain database IDs with `gh api repos/dapih/Calibre-Web-NextGen/issues/<n> --jq .id`; these are neither issue numbers nor node IDs. If dependencies are unavailable, put `Blocked by: #<n>, #<n>` at the top of the child body.
- **Count open blockers**: `gh api repos/dapih/Calibre-Web-NextGen/issues/<n> --jq .issue_dependencies_summary.blocked_by`. A ticket is startable only when every blocker is closed. For a fallback body list, check each referenced issue's state.
- **Comment**: `gh issue comment <number> --body-file <comment.md>`.
- **Apply or remove labels**: `gh issue edit <number> --add-label "..."` or `--remove-label "..."`. Use the mapping in `docs/agents/triage-labels.md`.
- **Update an issue body**: `gh issue edit <number> --body-file <body.md>`.
- **Close**: post the explanation with `gh issue comment <number> --body-file <comment.md>`, then run `gh issue close <number>`. Post the comment separately even when the issue is already closed.

Before the first issue creation in a session, verify the target with
`gh repo view dapih/Calibre-Web-NextGen --json nameWithOwner`.
File work in this repository; confirm a different target with the user.

## Pull requests as a triage surface

**PRs as a request surface: no.**

If this flag is later changed to `yes`, triage external PRs using the same
labels and states as issues:

- **Read**: `gh pr view <number>`, then `gh pr view <number> --comments`, and `gh pr diff <number>`. Read the body separately from comments.
- **List external PRs**: `gh api --paginate 'repos/dapih/Calibre-Web-NextGen/pulls?state=open' --jq '.[] | select(.author_association | IN("OWNER","MEMBER","COLLABORATOR") | not) | {number, title, author: .user.login, author_association, labels: [.labels[].name]}'`.
- **Comment, label, close**: `gh pr comment <number> --body-file <comment.md>`, `gh pr edit <number> --add-label "..."` / `--remove-label "..."`, and `gh pr close <number>`.

GitHub shares issue and PR numbers. Resolve an ambiguous number with
`gh pr view <number>`, falling back to `gh issue view <number>`.

## Skill instructions

When a skill says **publish to the issue tracker**, create a GitHub issue.
When it says **fetch the relevant ticket**, read that GitHub issue.

## Wayfinding operations

Used by `/wayfinder`. A map is one issue with child issues as tickets.

- **Map**: an issue labelled `wayfinder:map`, with Notes / Decisions-so-far / Fog in its body.
- **Child ticket**: link it as a sub-issue, or use the parent task list and `Part of #<map>` fallback above. Label it `wayfinder:<type>`, where type is `research`, `prototype`, `grilling`, or `task`. Create missing wayfinder labels when that workflow is invoked.
- **Frontier**: inspect all open children of the map; exclude tickets with an open blocker or an assignee. Pick the first eligible ticket in map order.
- **Claim**: `gh issue edit <number> --add-assignee "@me"`, before other writes in the wayfinding session.
- **Resolve**: comment with the answer, close the ticket, then update the map's Decisions-so-far with a brief finding and a link.
