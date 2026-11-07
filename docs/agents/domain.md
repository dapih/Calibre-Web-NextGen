# Domain docs

This repository uses one domain context.

## Before exploring

Read the root `GLOSSARY.md` for domain terms and the ADRs in `docs/adr/`
that concern the area being explored.

If these documents do not exist, continue silently. The `domain-modeling`
skill creates them as terms and architectural decisions are resolved;
setup does not create placeholder domain documentation.

## Layout

- `GLOSSARY.md`: shared domain vocabulary at the repository root.
- `docs/adr/`: architectural decision records for this repository.

If the project later adopts a root `GLOSSARY-MAP.md`, read it and the
context glossaries relevant to the task. Also check `src/<context>/docs/adr/`
for scoped decisions.

## Use the glossary's vocabulary

Use the glossary's defined terms in issue titles, proposals, hypotheses,
and tests. If a needed concept is missing, check whether existing terminology
covers it; otherwise note the gap for `domain-modeling`.

## Surface ADR conflicts

When a proposal conflicts with an existing ADR, name that ADR and explain
why its decision should be reconsidered before proceeding.
