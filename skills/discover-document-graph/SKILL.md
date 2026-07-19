---
name: discover-document-graph
description: Start document-driven development from a PRD by inspecting project context, interviewing the user one question at a time, comparing technical approaches, and proposing the minimum project-specific document graph for explicit approval. Use for new projects, major architectural changes, or repositories that have a PRD but no approved docs/document-manifest.json. Do not use to draft a single artifact after the graph already exists.
---

# Discover Document Graph

Turn a PRD into an explicitly approved plan for project documents. Fix the
decision process, not the document list. Do not implement, scaffold product
code, or create the manifest before the user approves the graph.

## 1. Inspect context

Read the PRD completely. Inspect existing repository instructions, architecture
documents, source layout, deployment configuration, migrations, and recent
history when available. Distinguish confirmed requirements from assumptions,
ideas, and unresolved choices.

If the PRD describes several independently deployable or independently decided
systems, propose a decomposition before detailed architecture questions.

## 2. Interview one decision at a time

Ask exactly one question per message. Prefer a short multiple-choice question
when the alternatives are known; allow the user to modify or reject every
option. Do not ask for information already stated in the PRD or repository.

Use these as discovery lenses, never as a mandatory document checklist:

- persistent data and lifecycle
- internal or external interfaces
- identity, roles, tenants, and authorization
- realtime behavior, concurrency, and consistency
- deployment environment and network constraints
- operations, failure recovery, and observability
- security, privacy, compliance, and sensitive data
- performance, availability, scale, and cost targets
- vendors, portability, quotas, and lock-in
- AI evaluation, provenance, and human review

Record confirmed decisions and open issues in the conversation. When a decision
has meaningful alternatives, present 2-3 approaches, lead with a recommendation,
and explain the trade-offs. Serverless, self-managed, and on-premises choices
must lead to different consequences rather than different boilerplate.

## 3. Decide whether to merge or split artifacts

Create a separate artifact only when it has an independent audience, approval
boundary, change cadence, operational owner, or enough complexity that combining
it would obscure decisions. Merge closely coupled concerns for small projects.
Omit a document when its decisions are absent or adequately covered elsewhere.

Do not assume names such as `ERD.md`, `DATABASE.md`, or `SECURITY.md`. Paths and
titles are outcomes of this project conversation.

## 4. Propose the graph

Present the smallest useful graph with, for every proposed artifact:

- stable id and proposed path
- purpose and decisions it owns
- `informed_by` sources
- `depends_on` artifact ids
- dynamic `required_for` implementation scopes
- why it is separate rather than merged

Also list intentionally omitted documents and where their decisions will live.
Show at least one leaner alternative when the recommendation has more than two
artifacts. Review the proposal in sections scaled to complexity and revise it
until the user explicitly approves the complete graph.

## 5. Create the manifest only after approval

After explicit approval, create `docs/document-manifest.json` in one coherent
edit. Use this shape:

```json
{
  "schema_version": "1.0",
  "source": {"prd": "docs/PRD.md"},
  "artifacts": [
    {
      "id": "project-specific-id",
      "path": "docs/project-specific-name.md",
      "purpose": "Decision boundary owned by this artifact",
      "status": "proposed",
      "informed_by": ["prd"],
      "depends_on": [],
      "required_for": ["project-specific-scope"]
    }
  ],
  "implementation_gate": {
    "require_relevant_documents_approved": true,
    "require_traceability": true
  }
}
```

Keep the artifact order topological: foundations before dependents. Validate the
JSON structure with the plugin's `scripts/docflow.py validate`. Proposed artifact
files may not exist yet; do not mark them approved.

## 6. Handoff

State which artifact should be authored first and why. Continue only with
`author-project-document`. No implementation skill may run until every artifact
relevant to that work is explicitly approved.

## Non-negotiable gates

- One question at a time.
- 2-3 approaches for consequential choices.
- No fixed artifact checklist.
- No manifest before explicit graph approval.
- No code or product scaffolding during discovery.
- Silence, inferred preference, and an existing draft are not approval.
