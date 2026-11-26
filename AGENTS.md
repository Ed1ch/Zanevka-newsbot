# AGENTS.md

## Project

Zanevka Newsbot is one project with two components:

- **Python Newsbot** — the root-level Python modules that process `.eml` news, documents, images and VK materials and publish to WordPress.
- **WebUI** — the existing HTML/CSS/JavaScript interface in `webui/`, served by Flask/Waitress in `webui_server.py`. Authenticated API launches `prelaunch.py` as a separate process, edits allowlisted config literals and streams real journal lines through one shared SSE reader.

Keep the batch pipeline independent of the API. Deployment and HTTP contracts are in `docs/operations/deployment.md` and `docs/ui/api.md`; do not redesign the existing interface during backend integration.

Documentation map: [docs/README.md](docs/README.md). Use it to select the documents relevant to the task. Approved architectural decisions in [docs/decisions.md](docs/decisions.md) are mandatory constraints.

## Sources of truth

- Current source code describes the actual implementation. Use the current target branch, not stale chat context.
- Approved architectural decisions define mandatory project constraints. Consult `docs/decisions.md` before non-trivial changes.
- Consult only the other documentation relevant to the task, using the documentation map.
- Inspect affected source files, interfaces and their usages before making changes. For integration tasks, inspect both Python and WebUI before proposing or changing interfaces.
- If implementation conflicts with an approved architectural decision, report the discrepancy to the owner. Do not silently change either the implementation or the decision.

## Project scale and engineering principles

Zanevka Newsbot is a small, internal administrative application, not a commercial SaaS product.

### Scale and deployment

- Expected users: approximately 3 trusted administrators.
- Deployment: one application instance on Ubuntu RELEASE.
- Workload: small, periodic batches of incoming news, typically processed every 2 hours.
- The application is not designed for public registration, multi-tenancy, high traffic, or horizontal scaling.
- The primary goal is a reliable, complete, maintainable tool that its administrators can understand and support.

### Engineering principles

1. **Prefer the simplest solution that satisfies the actual requirements.** Complexity must be justified by a concrete problem, not hypothetical future growth.
2. Reuse existing modules, Python standard-library capabilities, and established project dependencies before introducing new technologies.
3. Do not introduce microservices, message brokers, distributed task queues, external caches, databases, container orchestration, or heavyweight frontend frameworks unless a specific requirement clearly justifies them and the user approves the architectural change.
4. Prefer local files and existing application mechanisms when they are sufficient, while preserving atomicity, concurrency safety, and data integrity.
5. Avoid premature optimization, speculative scalability, unnecessary abstractions, and infrastructure intended for large organizations.
6. Keep deployment, configuration, debugging, recovery, and maintenance understandable for a small administrator team.
7. A new dependency or infrastructure component must provide a clear benefit that outweighs its installation and maintenance costs.
8. **Simplicity must not compromise correctness, security, data integrity, error recovery, or testability.**
9. Before proposing a substantial architectural addition, explain the concrete problem it solves, why the existing design is insufficient, and its operational costs. Obtain user approval before implementing it.

### Definition of success

The project succeeds when the agreed functionality works reliably in the target environment, can be verified with practical tests, and remains maintainable without dedicated development or DevOps personnel.

Do not optimize for enterprise-scale benchmarks, architectural sophistication, or technology adoption as goals in themselves.

## Development workflow

For non-trivial changes use **PLAN -> PATCH -> REVIEW**.

### PLAN

Identify affected modules, interfaces and regression risks. Do not modify code yet when the user asks to review the plan first.

Before PATCH, identify the target Git branch and verify that the requested changes are authorized for that branch. If the target branch is unclear, ask before modifying repository files.

### PATCH

Make the smallest change that solves the task. Do not perform unrelated refactoring.

### REVIEW

Review the diff for regressions, broken contracts, unnecessary complexity and duplicated functionality. Follow `docs/operations/trial-run.md`: run targeted checks needed for the change in Codex Cloud; reuse applicable CI results for the same SHA instead of duplicating the full regression suite. Without applicable CI, perform necessary checks in the available environment. Report blocked or unrun checks and their risks; do not weaken tests or claim success. Do not build dedicated development infrastructure for routine tasks.

## Development rules

- Prefer small, targeted changes over unnecessary rewrites.
- Preserve existing behavior unless the task explicitly requires changing it.
- Keep Python processing logic and UI presentation logic separate. Avoid modifying the other component for an unrelated task.
- Do not introduce an API, server framework, data contract or process-management mechanism without examining existing code and agreeing a concrete integration plan.
- Do not silently change architecture or public contracts between modules.
- Search for existing functionality before adding a helper or module.
- Keep modules responsible for their own domain.
- Keep **all** Python Newsbot tests, diagnostic/probe scripts and their test fixtures under `probe/`, not in the repository root. Preserve production module boundaries; when moving test scripts, check imports and file paths.
- Prefer simple, explicit Python over clever abstractions.
- Do not introduce new architectural patterns, base classes, factories, async code or abstraction layers unless they solve a concrete problem in the current task.
- Do not hardcode credentials, tokens or other secrets.
- Keep temporary files out of Git.

- Do not invent APIs, data structures or project behavior. Inspect the actual code first.
- When uncertain, state the uncertainty rather than guessing.

## Architectural decisions

When a task results in a significant architectural or design decision, proactively tell the user that it may be worth recording in `docs/decisions.md`.

Do not update `docs/decisions.md` automatically unless the user explicitly approves it. ADR identifiers are permanent and must not be reused. Editorial corrections must preserve the substance of approved decisions.

Examples include:

- changing module responsibilities or boundaries;
- introducing or removing an architectural pattern;
- changing important interfaces between modules;
- choosing a long-term approach when multiple alternatives exist;
- establishing a project-wide development or processing rule;
- accepting an important architectural tradeoff.

Do not suggest a `docs/decisions.md` entry for routine bug fixes, minor implementation details, formatting, or temporary experiments.

## Important project invariants

A failure while processing one news item must **not terminate the entire processing loop**.

Cleanup of the downloaded `.eml` and `extracted_images/` must happen even when processing fails.

VK objects may occur inside nested `attachments`. Preserve recursive attachment handling when modifying VK processing.

`prelaunch.py` owns the shared process lock and external launch mode. `main.py` is the single batch orchestration layer; `integrity_check.py` returns a pure completeness report. External callers must use `prelaunch.main()` so the lock covers processing and cleanup. Domain-specific parsing, VK processing and WordPress logic remain in their relevant modules.

## WebUI development

- Consult `docs/ui/admin.md` and relevant architectural decisions and contracts. Inspect affected HTML, CSS and JavaScript together, including their dependencies.
- Preserve existing visual design, colors, dimensions, layout and behavior unless explicitly requested otherwise, including journal filters, zoom, scrolling, tabs, floating settings window, switches and themes.
- Do not rewrite event handlers, styles or DOM structure for cosmetic refactoring.
- Do not modify backend code for purely visual changes, or vice versa.
- Do not introduce frontend frameworks or dependencies without justification and approval.
- Run, status, operational settings and journals use the Flask/Waitress API. Do not replace real API behavior with simulations or fabricated states, or store operational settings in localStorage.
- Do not store secrets in localStorage, source code or Git. Preserve login, cookie-session and CSRF protections; settings GET returns only secret-presence indicators.
- For behavior changes, identify regression risks and verify affected browser scenarios and related controls. Review the diff for unrelated changes.

## Documentation policy

- Documentation describes only the current, implemented state of the project.
- Do not document planned features, unimplemented functionality, hypothetical architecture or future development stages as part of the project documentation.
- Planned changes and development tasks belong in GitHub Issues.
- Implementation history belongs in Git history and pull requests.
- Each subject must have a single authoritative document. Use links instead of duplicating its contents.
- When implementation changes, update the affected documentation in the same change.
- Remove obsolete descriptions, instructions and references instead of accumulating historical amendments.
- Do not claim successful testing, deployment or platform compatibility without verification.

## Git and repository safety

GitHub is the canonical repository for source code and documentation.

- Codex may commit and push to the designated working branch within an approved task without separate approval for each commit or push.
- Verify the destination branch, scope, diff and absence of secrets before writing or pushing. If the target branch is unclear, ask before modifying files.
- **Do not transfer code or other non-documentation changes from a working branch into `main` without explicit owner approval of the specific source branch, target branch and changes.** A request to implement, test, commit or push a change is not authorization to merge it into `main`. Report the branch and commit and wait for approval. This applies equally to merge, cherry-pick, copying, recreating or manually reimplementing changes.
- Creating PRs and merging without separate approval is permitted only for documentation-only changes within the approved task. Before merge, confirm the diff contains no code, configuration, CI, scripts or other operational files and that any applicable CI checks have passed. This does not waive approval of the documentation change itself where required, including architectural decisions.
- Direct changes to `main` require explicit authorization for the specific change. Force pushes, deployment and changes to production configuration require separate explicit approval.
- Commit meaningful, reviewable states. Avoid unrelated changes in the same commit. Never commit secrets or local-only configuration.
- Never create or use a repository fork without explicit owner permission, including as a fallback when direct repository access is unavailable.

**When in doubt, stop and ask.**

## Environment

- Development and targeted checks are performed in Codex Cloud; applicable CI handles independent regression checks.
- GitHub is the canonical repository.
- Ubuntu RELEASE (`mcpv3release`) is the deployment and operational environment.
- No persistent local development environment is maintained.
- Do not develop or modify application source code directly on RELEASE. RELEASE is for deployment, operation and visual/operational acceptance only; do not run automated test suites or install diagnostic dependencies there.
- Scheduled Newsbot runs are managed externally through cron.
- Preserve cross-platform compatibility unless the owner explicitly changes the supported-platform policy. Avoid OS-specific assumptions in shared application logic; verify dependencies, Pandoc and filesystem behavior on each platform before claiming compatibility.
