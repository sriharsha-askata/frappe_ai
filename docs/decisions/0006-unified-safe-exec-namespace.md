# ADR 0006 — One hardened `safe_exec` namespace for all sandboxed code

**Status:** Accepted
**Date:** 2026-08-05
**Deciders:** Sri Harsha Dabbiru

---

## Why Agno does not remove this requirement

A reasonable first assumption is that adopting Agno makes `safe_exec` unnecessary. It does
not — the two solve different problems and neither substitutes for the other.

| | Agno | `safe_exec` |
|---|---|---|
| **Solves** | Which tool to call, with which arguments | What executing code is permitted to reach |
| **Mechanism** | LLM tool-calling loop + JSON Schema + pydantic validation | RestrictedPython compilation + an allowlisted namespace |
| **Applies to** | The tool *interface* | The tool *implementation* |

Agno validates that `read(doctype="Sales Order", limit=20)` matches the declared schema.
It never inspects what the `read` function body does, and a function registered with Agno
is an ordinary Python callable with full interpreter access.

`frappe_ai` executes code from two untrusted-or-semi-trusted sources that no orchestration
framework can constrain:

1. **The `execute` builtin** — the LLM writes Python at runtime and it is then run.
   Untrusted by construction. Agno would pass the string through; only a sandbox decides
   whether that string can reach `frappe.db.sql`.
2. **Script `AI Tool` rows and `AI Trigger.condition`** — Python stored in DocType fields.
   Configuration, not deployed code: not code-reviewed, not version-controlled, editable by
   anyone with write permission on the DocType.

Combined with [ADR 0003](0003-tools-execute-in-frappe.md) — tools execute inside Frappe
under the acting user's identity — removing the sandbox would allow LLM-authored code to
run with unrestricted database access. Prompt injection through a document or knowledge
chunk would then reach arbitrary SQL. The sandbox is what makes ADR 0003's guarantee real
rather than nominal.

---

## Context

`flow` executes user- and LLM-supplied Python in three places, and — this is the problem —
**not with the same sandbox**:

| Site | Source of code | Sandbox used |
|---|---|---|
| `execute` builtin tool | **the LLM** | `flow/utils/safe_exec.py` (hardened) |
| Script `Flow Tool` rows | an administrator | `frappe.utils.safe_exec` (**broad**) |
| `Flow Trigger.condition` | an administrator | `frappe.utils.safe_exec` (**broad**) |

`flow/utils/safe_exec.py` reuses frappe's RestrictedPython machinery but builds its **own
allowlisted namespace** in which every data function is permission-enforcing. It
deliberately **excludes**:

- `frappe.db.sql` — arbitrary SQL, bypasses all permissions
- `frappe.qb` — query builder, same problem
- `frappe.db.set_value` — writes without validation or permission checks
- `frappe.get_all` — reads **without permission filtering**

and provides gated replacements: `_gated_get_doc` (checks `read`), `_gated_get_meta`,
`_gated_get_print`, and `_permissioned_get_list`, which strips `ignore_permissions`,
`ignore_user_permissions`, and `user` kwargs and forces `user=frappe.session.user`.

`frappe.utils.safe_exec` — frappe's standard server-script namespace — provides
`frappe.db.sql` and `frappe.get_all`.

### The asymmetry

The `execute` tool's docstring describes a narrow, permission-checked sandbox. That is
accurate for `execute`. But **Script tools and trigger conditions run in the broader
namespace**, so code stored in a `Flow Tool` row can call `frappe.db.sql` and read or write
anything, ignoring permissions entirely.

This inverts the intended risk ordering. LLM-generated code (untrusted, generated fresh per
call) gets the *tight* sandbox; code stored in a DocType field gets the *loose* one. Since
Script tools are invoked by agents on behalf of arbitrary users, a carelessly authored
Script tool becomes a permission-bypass primitive reachable through ordinary conversation.

There is no sign this was intentional; it reads as drift, with `resolver.py:11` and
`conditions.py` importing the frappe helper directly while the `execute` path was hardened
later.

---

## Decision

**`frappe_ai` has exactly one sandbox namespace: the hardened one.**

All three execution sites route through `frappe_ai/utils/safe_exec.py`:

| Site | Namespace |
|---|---|
| `execute` builtin | hardened |
| Script `AI Tool` rows | **hardened** (changed from `flow`) |
| `AI Trigger.condition` | **hardened** (changed from `flow`) |

`frappe.utils.safe_exec` is not imported anywhere in `frappe_ai`. Its RestrictedPython
*machinery* (compilation, guards, `FrappePrintCollector`, `NamespaceDict`) is still reused —
only the **namespace** is replaced.

Schema derivation for Script tools continues to use `schema_from_code()`, which reads the
AST **without evaluating** it, so untrusted code never executes during schema derivation.

---

## Consequences

### Positive

- **Permission enforcement is uniform.** No execution path can read or write outside the
  acting user's permissions.
- **[ADR 0003](0003-tools-execute-in-frappe.md) becomes enforceable.** That ADR guarantees
  tools cannot exceed the acting user's permissions; the guarantee only holds if the
  sandbox enforces it on every path.
- **Documented behaviour matches actual behaviour.** `flow` described a sandbox that two of
  three call sites did not use.
- **One namespace to audit.** Adding a capability becomes one deliberate decision instead
  of accidental divergence between call sites.

### Negative

- **Script tools relying on `frappe.db.sql` or `frappe.get_all` break.** None exist in this
  greenfield app ([ADR 0005](0005-greenfield-no-migration.md)), but anyone hand-porting a
  `flow` script must rewrite it against `_permissioned_get_list` and `_gated_get_doc`.
- **Some legitimate scripts get harder to write.** Aggregations natural in SQL must be
  expressed as permission-respecting list calls — more verbose, and slower on large datasets.
- **Trigger conditions lose SQL.** Conditions needing cross-DocType aggregates must use
  `get_value`/`count`/`get_list`, or move the logic into an Imported tool.

### Escape hatch for genuinely privileged work

Where unrestricted access is truly required, the supported path is an **Imported `AI Tool`** —
a Python function in an installed app, referenced by `import_path`. Such code is:

- reviewed and version-controlled in a repository, not typed into a DocType field
- deployed through the normal app-release process
- unambiguously a code change rather than a configuration change

This preserves the capability while keeping it out of reach of DocType edits.

---

## Alternatives Considered

### Rely on Agno alone, drop `safe_exec`
Rejected — see *Why Agno does not remove this requirement* above. Agno constrains tool
interfaces, not tool implementations, and offers nothing for runtime-generated code.

### Drop `execute` and Script tools entirely
No runtime code execution at all: only Imported tools plus the seven other builtins, and a
non-Python expression form for conditions. Strictly the safest option and genuinely
tempting.
**Rejected** because `execute` is central to the built-in assistant's operating doctrine —
it is what lets the agent handle tasks the fixed builtins do not cover — and Script tools
are the main way administrators extend the system without a code deploy. Removing both
would be a real capability regression against `flow`.

### Keep `flow`'s asymmetry
Rejected. Carrying a known permission bypass into a new app because the old app had it is
not a justification.

### Use the broad namespace everywhere
Simpler, and matches frappe's server-script convention.
**Rejected:** it would hand LLM-generated `execute` code `frappe.db.sql`. With prompt
injection via a document or knowledge chunk, that is a direct path from "user uploads a
malicious file" to arbitrary SQL.

### A per-tool `trust_level` field selecting the namespace
Flexible, letting administrators opt specific tools into the broad namespace.
**Rejected for now:** it makes security posture a per-row configuration choice, easy to
override and hard to audit across many rows. The Imported-tool path already serves the
legitimate need with better review properties. Revisit only if a concrete case cannot be
handled that way.

---

## Verification

- Grep confirms `frappe.utils.safe_exec` is not imported anywhere in `frappe_ai`.
- A Script `AI Tool` calling `frappe.db.sql` raises inside the sandbox.
- A Script `AI Tool` calling `frappe.get_all` raises inside the sandbox.
- A Script tool calling `frappe.get_list` returns only rows the **acting user** may read,
  and passing `ignore_permissions=True` does not widen the result.
- An `AI Trigger.condition` using `frappe.db.sql` fails validation, or fails closed at
  evaluation and is treated as "not met".
- The `execute` tool behaves identically to `flow`.

---

## References

- [001 — Architecture §4](../specifications/001-architecture.md)
- [ADR 0003 — Tools execute in Frappe](0003-tools-execute-in-frappe.md)
- `apps/flow/flow/utils/safe_exec.py` — the hardened namespace being adopted everywhere
- `apps/flow/flow/lib/resolver.py:11` — the `frappe.utils.safe_exec` import being replaced
- `apps/flow/flow/utils/conditions.py` — the second call site being replaced
