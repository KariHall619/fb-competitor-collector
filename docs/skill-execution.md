# Codex Skill Execution Model

This project follows Codex's official customization model:

- `AGENTS.md` is repository guidance. Codex reads it before work starts, so it must be short, stable, and focused on hard project rules.
- `SKILL.md` is a reusable workflow. Codex sees the skill name/description first and loads the full file only when the task matches, so it should be focused on trigger conditions, routing, and recovery decisions.
- Detailed architecture, data contracts, script reference, and troubleshooting belong in separate docs and should be loaded only when needed.

## Project Application

Keep the top-level files small:

- `AGENTS.md`: repo invariants, non-regression rules, validation expectations, and pointers to detailed docs.
- `SKILL.md`: natural-language request mapping, preflight order, hard blockers, primary commands, and final reporting expectations.
- `README.md`: human quick start and directory map.
- `README_FOR_OPERATOR.md`: no-code operator runbook.

Detailed references:

- `docs/architecture.md`: end-to-end data flow and runtime boundary.
- `docs/data-contract.md`: fields, Feishu output, quality gate, and status meanings.
- `docs/script-reference.md`: maintained script map and command examples.
- `docs/troubleshooting.md`: recovery order for Feishu, OpenCLI, Facebook login, and partial jobs.

## Design Rules

1. Do not duplicate the same long rule in multiple top-level files. Put the detailed version in `docs/` and link to it.
2. Keep `SKILL.md` imperative and executable: identify scope, run preflight, choose one entrypoint, follow `next_commands`, report status.
3. Keep `AGENTS.md` durable: avoid last-run observations, branch-specific notes, and stale runtime state unless they are still active non-regression facts.
4. Prefer project-owned deterministic scripts for browser capture and Feishu sync; Codex orchestrates and summarizes.
5. Keep runtime data out of source control: `data/`, `exports/`, SQLite files, Chrome profiles, debug screenshots, and generated summary payloads.

## Source Basis

The current Codex manual states that skills use progressive disclosure: Codex starts with skill name, description, and file path, then reads the full `SKILL.md` only when it selects that skill. It also states that Codex reads `AGENTS.md` before doing work and combines project instruction files into the prompt. Those two facts are why this project keeps `AGENTS.md` and `SKILL.md` concise and moves detailed references into separate docs.
