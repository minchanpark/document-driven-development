# Document-driven development

When a repository contains `docs/document-manifest.json`, treat the approved document graph as the implementation contract.

- Do not edit implementation files until the relevant documents are approved and a valid task context lock exists.
- If implementation requires a design change, update and re-approve the document or ADR before changing code.
- Keep requirement-to-code-to-test traceability current and run the repository verification gate before declaring completion.
- Use the plugin skills to discover the document graph, author artifacts, prepare a documented change, and verify the result.
