# Conventions

This file is Pythia's design guide for Audaciter development. It captures our preferred naming patterns, data shapes, module structure, coding style, and architectural discipline — the accumulated decisions that keep our codebases consistent across sessions, context chops, and long breaks.

**This is a living document.** We can add to it or amend it whenever our thinking evolves. If a convention stops serving us, we change it. The goal isn't rigidity — it's continuity.

**This is a shared document.** These conventions apply across all Audaciter projects. Project-specific extensions (specialised data shapes, pipeline stages, domain types) live in the project's own documentation. This file covers the universals.

---

## 1. Naming & Style

### Variables and Functions
- `snake_case` for all variables and functions
- Descriptive names: `attack_roll`, `damage_result`, `build_context`
- Boolean variables prefixed with `is_`, `has_`, `can_`, `should_`
- Module-level private functions prefixed with `_`

### Classes
- `PascalCase` for all classes
- Dataclasses preferred for data containers and results
- Enums with `UPPER_SNAKE_CASE` members

### Constants
- `UPPER_SNAKE_CASE` for module-level constants

### Files and Modules
- `snake_case.py` for all filenames
- One logical unit per file — a module does one thing
- Module-level docstring with structured metadata (see Section 7) followed by a description of the module's purpose
- **File size guideline**: If a file exceeds ~400 lines, strongly consider whether it should be broken into smaller modules. Some files are large by necessity — a multi-stage pipeline orchestrator, for example, cannot be meaningfully split without creating more problems than it solves. These are exceptions, not the norm. Large files benefit from lightweight section markers (see Section 7) to aid navigation.

### Imports
- Full package import paths, never relative imports
- Standard library → third party → project modules, separated by blank lines

---

## 2. Data Shapes

Use dataclasses for all structured data that crosses module boundaries. **Never use `Dict[str, Any]` for anything passed between modules.** If a structure needs a shape, give it a proper dataclass with typed fields.

Rules:
- Prefer small, focused dataclasses over large ones with many optional fields
- Use composition when a result needs additional context
- Separate input types (actions, requests) from output types (results, evaluations) clearly

Project-specific data shapes are defined in each project's documentation. The principle is universal: typed dataclasses, not dictionaries.

---

## 3. Module Patterns

### Pure Functions Preferred
Modules should be stateless where possible. A function takes inputs and returns results. Side effects are explicit and isolated.

### Error Handling
- Define domain-specific exceptions for each subsystem (e.g., `InvalidActionError`, `InsufficientResourcesError`, `StateInconsistencyError`) rather than reusing generic exceptions
- Domain exceptions live in a dedicated `exceptions.py` or in per-subsystem exception modules
- Raise specific exceptions at module boundaries
- Internal functions can assume valid data if the boundary check passed
- Never catch and silently ignore exceptions except at the top level of an orchestrator for graceful degradation

### Return Types
- Always return dataclasses for complex results
- Simple yes/no queries return `bool`
- Use `Optional` when absence is meaningful, not as a lazy default

---

## 4. Testing

- Test files mirror source: `provider.py` → `test_provider.py`
- Test function names describe the scenario: `test_fire_resistance_halves_fire_damage`
- One assertion per test where practical
- Use fixtures for shared setups
- Tests for shared types should be clearly identifiable as contract tests — they protect the integrity of the type definitions that multiple modules depend on

---

## 5. The Role of types.py

`types.py` is the single source of truth for shared type definitions. Every module that produces or consumes a shared data shape imports it from `types.py`.

Rules:
- No module defines its own version of a shared type
- If a data shape is used by multiple modules, it belongs in `types.py`
- Single-consumer types that genuinely serve only one module may live in that module, but the bias should be toward shared types. When in doubt, put it in `types.py` — it's easier to extract later than to reconcile divergent definitions
- `types.py` conforms to the data shape conventions in Section 2

---

## 6. Contracts and Interfaces

We follow a contracts-first development approach: define what a component promises before implementing how it delivers.

Rules:
- When adding new behaviour that multiple modules will use, define the contract before implementing consumers
- No module should depend on a concrete implementation when a contract will suffice
- Contracts can be expressed as documented interfaces, Protocol classes, or abstract base classes — use the lightest mechanism that serves the project's current scale
- The discipline matters more than the specific Python pattern. For a small codebase, a clearly documented interface in a docstring is sufficient. For a large one, Protocol classes earn their weight

---

## 7. Metadata

Every module includes structured metadata in its module-level docstring.

```python
"""
Module: provider.py
Dependencies: config.py, types.py

Handles all communication with the xAI API including streaming
and tool call processing.
"""
```

- **Module**: the filename
- **Dependencies**: project modules this file imports from (standard library and third-party packages are excluded)
- After the metadata, a blank line then a plain-English description of what the module does

Do not maintain a reverse-dependency field (e.g., "Dependent-on"). Reverse dependencies are better discovered through code search or tooling than through manual maintenance that inevitably goes stale.

No separate comment blocks above the docstring. The docstring is the single home for both metadata and description.

### Section Markers vs Metadata Blocks

There is a distinction between **metadata blocks** and **section markers**:

- **Metadata blocks** — module ID, version, dependencies expressed as standalone comment headers. These are redundant with the docstring and should not be used.
- **Section markers** — lightweight `# --- Stage 4: Attack Roll ---` style comments that help navigate a large file. These are encouraged in files that are large by functional necessity. They are navigation aids, not metadata, and serve a different purpose.

Small, focused modules typically don't need section markers. Large orchestration files benefit from them.

---

## 8. Development Workflow

When fixing or reviewing code, we work one directory at a time. This keeps the volume of potential errors manageable and prevents long chains of edits across the codebase without verification.

### Standard Process

1. **Fix all modules in the directory** — make them convention-compliant and internally consistent.
2. **Verification pass** — read every file back from the repo and cross-reference within the directory. Confirm field names, imports, constructor calls, and types all agree.
3. **Check interfaces with other directories** — read the consumers and providers outside the directory to identify mismatches.
4. **Fix only the current directory's files** — if a mismatch lives in another directory, document it for when we work on that directory. We don't chase fixes across boundaries.
5. **Move to the next directory** — repeat the process.

### Cross-Directory Interface Changes

When a change requires modifications on both sides of a directory boundary:

1. Define or update the relevant contract or data shape in `types.py` (or the appropriate contract location) first.
2. Implement both sides in the same focused pass.
3. Verify the interface after both sides are updated before moving to other work.

This exception exists to prevent interface drift while still preserving the discipline of the directory-by-directory approach.

---

## 9. Pipeline Architecture (Project-Specific)

This section is a template. Projects that use a staged pipeline (e.g., Storm's attack resolution) define their pipeline stages, orchestration patterns, and stage module conventions in their own documentation.

The universal principle: individual stage modules don't know about each other. They know about their inputs and outputs. An orchestrator module wires them together. **Stage modules must not import the orchestrator** — this prevents circular dependencies.
