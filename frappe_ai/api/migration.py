"""One-shot, repeatable migration from the retired native tool bindings.

The migration is intentionally data-driven and leaves a report.  It does not add
an AI Tool-to-FAC link: after a successful run the only durable binding is the
agent's ``AI Agent Plugin Tool.fac_tool`` row.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _


EMBEDDING_MODEL_PREFIXES = (
	"text-embedding-",
	"gemini-embedding-",
	"openai/text-embedding-",
	"gemini/gemini-embedding-",
)


def migrate_ai_tools() -> dict[str, Any]:
	"""Migrate legacy AI Tool selections to direct FAC bindings idempotently."""
	report: dict[str, Any] = {"migrated": [], "unmatched": [], "ambiguous": [], "skipped": []}
	if not frappe.db.exists("DocType", "AI Tool"):
		return {**report, "status": "already_migrated"}

	# Populate FAC Tool Configuration through Assistant Core's normal registry
	# synchronization before matching rows. This is safe to rerun.
	try:
		from frappe_assistant_core.utils.migration_hooks import _sync_tool_configurations

		_sync_tool_configurations()
	except Exception as exc:
		report["sync_error"] = str(exc)
	if not frappe.db.exists("DocType", "FAC Tool Configuration"):
		report["status"] = "blocked_no_fac_configuration"
		return report

	configs = frappe.get_all("FAC Tool Configuration", fields=["name", "tool_name"])
	by_tool_name: dict[str, list[str]] = {}
	for config in configs:
		by_tool_name.setdefault(config.tool_name or config.name, []).append(config.name)

	for legacy in frappe.get_all(
		"AI Tool", fields=["name", "slug", "requires_confirmation", "enabled"]
	):
		tool_name = legacy.slug or legacy.name
		matches = by_tool_name.get(tool_name, [])
		if not matches:
			report["unmatched"].append({"tool": tool_name, "reason": "no FAC Tool Configuration"})
			continue
		if len(matches) != 1:
			report["ambiguous"].append({"tool": tool_name, "matches": matches})
			continue

		for agent in frappe.get_all("AI Agent", fields=["name"]):
			legacy_rows = frappe.get_all(
				"AI Agent Tool", filters={"parent": agent.name}, fields=["tool"]
			)
			if not any((row.tool == legacy.name or row.tool == tool_name) for row in legacy_rows):
				continue
			already = frappe.db.exists(
				"AI Agent Plugin Tool", {"parent": agent.name, "fac_tool": matches[0]}
			)
			if already:
				report["skipped"].append({"agent": agent.name, "tool": tool_name})
				continue
			frappe.get_doc(
				{
					"doctype": "AI Agent Plugin Tool",
					"parent": agent.name,
					"parenttype": "AI Agent",
					"parentfield": "plugin_tools",
					"fac_tool": matches[0],
					"enabled": int(legacy.enabled),
					"requires_confirmation": int(legacy.requires_confirmation),
				}
			).insert(ignore_permissions=True)
			report["migrated"].append({"agent": agent.name, "tool": tool_name, "fac_tool": matches[0]})

	frappe.db.commit()
	report["status"] = "completed" if not (report["unmatched"] or report["ambiguous"]) else "completed_with_review"
	return report


def migrate_ai_model_types() -> dict[str, Any]:
	"""Mark legacy models as embeddings only when their IDs say so unambiguously.

	New and otherwise unidentifiable models retain the Chat default. The migration
	is repeatable and never changes a model already marked Embedding.
	"""
	report: dict[str, Any] = {"updated": [], "skipped": []}
	if not frappe.db.exists("DocType", "AI Model"):
		return {**report, "status": "not_installed"}

	for row in frappe.get_all("AI Model", fields=["name", "model_id", "model_type"]):
		if row.model_type == "Embedding":
			report["skipped"].append({"name": row.name, "reason": "already_embedding"})
			continue
		model_id = (row.model_id or "").strip().casefold()
		if not model_id.startswith(EMBEDDING_MODEL_PREFIXES):
			report["skipped"].append({"name": row.name, "reason": "not_identifiable"})
			continue
		frappe.db.set_value("AI Model", row.name, "model_type", "Embedding", update_modified=False)
		report["updated"].append(row.name)

	frappe.db.commit()
	report["status"] = "completed"
	return report


@frappe.whitelist()
def run_ai_tool_migration() -> dict[str, Any]:
	return migrate_ai_tools()
