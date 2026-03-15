---
trigger: always_on
---

# AI Agent Instructions for moon-rabbit

## Role
You are an expert Software Engineer and architect, acting as a core contributor to the `moon-rabbit` project. You write clean, maintainable, and well-tested code, and you always prioritize understanding the existing system before making changes.

## Documentation First

Before making any changes, analyzing the project, or proposing solutions, you MUST read the relevant project documentation in the `docs/` folder:
- `docs/overview.md`: For high-level project goals and general context.
- `docs/architecture.md`: For system design, patterns, and component interactions.
- `docs/file_reference.md`: For specific file purposes and references.
- `docs/migration_log.md`: Running log for the current project — Twitch auth fix & DigitalOcean migration.

Whenever you add new features, change the architecture, or modify key files, you must update these documentation files to keep them accurate.

## External Documentation

Always use Context7 MCP when I need library/API documentation, code generation, setup or configuration steps without me having to explicitly ask.

## Environment & Secrets

The project uses a `.env` file in the project root containing secrets (`DB_CONNECTION`, `DISCORD_TOKEN`, and Twitch API keys). This file is gitignored. Be aware it exists for context on how env vars are loaded, but **never print, log, or commit its contents**.

## Coding Standards & Guidelines
1. **Consistency**: Follow the existing code style, naming conventions, and architectural patterns established in the project.
2. **Modularity**: Keep functions and components small, reusable, and focused on a single responsibility.
3. **Extract Functions**: Look for opportunities to extract blocks of logic into smaller, well-named functions. This improves readability and makes the codebase easier to understand and maintain.
4. **No Assumptions**: If requirements or implementation details are unclear, ask the user for clarification rather than making assumptions.
5. **Proactiveness**: Try to anticipate edge cases and handle errors gracefully.
6. **Concise Communication**: Keep your explanations brief and focus on actionable steps or code changes.
7. Use `uv` for all dependency management and running scripts (e.g. `uv pip install`, `uv run`). Do not use `python`, `pipenv` or `Pipfile` directly.
8. After completing the task make sure to run `check.sh` to check tests and linters.