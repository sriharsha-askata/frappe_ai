"""Per-run tool and mutation budget enforcement (ADR 0008)."""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime


class BudgetExceeded(frappe.ValidationError):
	pass


def consume(run: str | None, *, mutation: bool = False, records: int = 1) -> None:
	if not run:
		return
	doc = frappe.get_doc("AI Run", run)
	if doc.status not in ("Running", "Paused"):
		raise BudgetExceeded(_("Run is no longer active."))
	snapshot = json.loads(doc.config_snapshot or "{}")
	limits = snapshot.get("budgets") or snapshot
	if doc.creation and (now_datetime() - get_datetime(doc.creation)).total_seconds() > limits.get("max_runtime_seconds", 600):
		raise BudgetExceeded(_("Run runtime budget exceeded."))
	usage = json.loads(doc.budget_usage or "{}")
	usage.setdefault("tool_calls", 0)
	usage.setdefault("mutations", 0)
	usage.setdefault("records", 0)
	usage["tool_calls"] += 1
	if mutation:
		usage["mutations"] += 1
	if records > limits.get("max_records_per_call", 100):
		raise BudgetExceeded(_("Tool call exceeds the maximum records-per-call budget."))
	if usage["tool_calls"] > limits.get("max_tool_calls", 50):
		raise BudgetExceeded(_("Tool-call budget exceeded for this run."))
	if usage["mutations"] > limits.get("max_mutations", 20):
		raise BudgetExceeded(_("Mutation budget exceeded for this run."))
	usage["records"] += records
	doc.db_set("budget_usage", json.dumps(usage), update_modified=False)
