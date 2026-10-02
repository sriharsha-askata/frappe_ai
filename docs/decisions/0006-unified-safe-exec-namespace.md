# ADR 0006 — One hardened sandbox for all code that is not deployed code

**Status:** Accepted · **Date:** 2026-08-05

## The problem

The app runs Python that nobody deployed through a normal release, in three places:

| Place | Who wrote the code |
|---|---|
| The `execute` tool | **The model**, written fresh for each call |
| Script `AI Tool` records | An administrator, stored in a DocType field |
| `AI Trigger.condition` | An administrator, stored in a DocType field |

In the older app these did **not** share a sandbox. `execute` used a hardened one, but script tools and trigger conditions used Frappe's standard server-script sandbox, which allows `frappe.db.sql` and `frappe.get_all` (raw SQL and reads that ignore permissions). So the *least* trusted code (the model's) got the tight sandbox and stored scripts got the loose one. A carelessly written script tool became a way to bypass permissions through an ordinary chat.

## Why the agent library does not remove the need

Agno checks that a tool call matches its declared schema. It never looks at what the tool's code does. A function registered with Agno is ordinary Python with full access. Only a sandbox limits what code can reach. Combined with [ADR 0003](0003-tools-execute-in-frappe.md) (tools run inside Frappe), removing the sandbox would let model-written code run raw SQL, and a prompt injected through a document could reach it.

## The decision

**There is exactly one sandbox namespace, the hardened one, in `frappe_ai/utils/safe_exec.py`.** All three places use it. `frappe.utils.safe_exec` is not imported anywhere in this app. Frappe's RestrictedPython machinery (compiling, guards, print capture) is reused; only the *set of allowed names* is replaced.

What the namespace leaves out: `frappe.db.sql`, `frappe.qb`, `frappe.db.set_value`, and `frappe.get_all`.

What it provides instead: permission-checked versions, such as `frappe.get_list` (which ignores `ignore_permissions`, `ignore_user_permissions` and `user` arguments and always uses the current user), `frappe.get_doc` and `get_last_doc` (check read permission), `frappe.get_meta`, `frappe.db.get_value`, `exists`, `count`, and the built-in tools as plain functions (except `execute` itself and `search_knowledge`).

A script tool's schema is derived by reading its syntax tree, **without running it**.

## What follows

**Good**
- Permission enforcement is the same on every path, so [ADR 0003](0003-tools-execute-in-frappe.md)'s guarantee is real.
- The documented sandbox is the sandbox that runs.
- One place to audit and extend.

**Costs**
- Scripts that use `frappe.db.sql` or `frappe.get_all` do not work and must be rewritten with permission-checked calls. Aggregations get more verbose and slower on large data.
- Trigger conditions cannot use SQL; use `get_value`, `count`, `get_list`, or move the logic into a code tool.
- **The sandbox is a safety net, not a wall.** It is RestrictedPython with an allow-list. The namespace still exposes some side-effecting helpers (`frappe.sendmail`, `frappe.enqueue`, `frappe.call`), and this module sets no CPU, time or memory limit. Every use of `execute` requires the user's approval for that reason.

**Escape hatch for privileged work:** write an **imported tool**, a Python function in an installed app. It is reviewed, versioned, and deployed like code, not edited in a form.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| Rely on Agno alone | It constrains tool interfaces, not tool code |
| No runtime code at all | Safest, but `execute` is how the assistant handles tasks the fixed tools do not cover, and script tools are how admins extend the app without a deploy |
| Keep the older asymmetry | Carrying a known permission bypass into a new app is not a reason |
| One broad namespace everywhere | Would give model-written code raw SQL |
| A per-tool "trust level" setting | Makes security a per-record choice that is easy to change and hard to audit; imported tools already cover the need |

## How we check

- No import of `frappe.utils.safe_exec` in the app.
- A script calling `frappe.db.sql` or `frappe.get_all` fails inside the sandbox.
- `frappe.get_list` returns only rows the acting user may read, even with `ignore_permissions=True` passed.
- A trigger condition using SQL fails validation, or fails closed at run time (treated as "not met").

Tests: `frappe_ai/tests/test_safe_exec.py`, `test_conditions.py`, `test_tool.py`.

## Related

[001 §5](../specifications/001-architecture.md), [008](../specifications/008-how-to-create-tools.md).
