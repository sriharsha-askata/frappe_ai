"""One-shot, repeatable migration from the retired native tool bindings.

The migration is intentionally data-driven and leaves a report.  It does not add
an AI Tool-to-FAC link: after a successful run the only durable binding is the
agent's ``AI Agent Plugin Tool.fac_tool`` row.

Retirement machinery: run manually per site (see docs/ai-tool-retirement.md)
before the deletion release is migrated. Remove in Stage 2, once every site has
migrated and the rollback window has ended.
"""

from __future__ import annotations

from typing import Any

import frappe


def migrate_ai_tools() -> dict[str, Any]:
	"""Migrate legacy AI Tool selections to direct FAC bindings idempotently."""
	report: dict[str, Any] = {"migrated": [], "unmatched": [], "ambiguous": [], "skipped": [], "conflicts": []}
	if not frappe.db.exists("DocType", "AI Tool"):
		return {**report, "status": "already_migrated"}

	# Populate FAC Tool Configuration through Assistant Core's normal registry
	# synchronization before matching rows. This is safe to rerun.
	from frappe_assistant_core.utils.migration_hooks import _sync_tool_configurations

	_sync_tool_configurations()
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
			legacy_rows = (
				frappe.get_all("AI Agent Tool", filters={"parent": agent.name}, fields=["tool"])
				if frappe.db.table_exists("AI Agent Tool") else []
			)
			config_rows = (
				frappe.get_all(
					"AI Agent Tool Config", filters={"parent": agent.name},
					fields=["tool_name", "source", "enabled"],
				) if frappe.db.table_exists("AI Agent Tool Config") else []
			)
			manual_configs = [
				row for row in config_rows
				if row.source != "mcp" and row.tool_name in {legacy.name, tool_name}
			]
			if not any(row.tool in {legacy.name, tool_name} for row in legacy_rows) and not manual_configs:
				continue
			already = frappe.db.get_value(
				"AI Agent Plugin Tool", {"parent": agent.name, "fac_tool": matches[0]},
				["enabled", "requires_confirmation"], as_dict=True,
			)
			if already:
				if int(already.enabled) != int(bool(legacy.enabled) and all(bool(row.enabled) for row in manual_configs)) or int(already.requires_confirmation) != int(legacy.requires_confirmation):
					report["conflicts"].append({"agent": agent.name, "tool": tool_name})
				else:
					report["skipped"].append({"agent": agent.name, "tool": tool_name})
				continue
			frappe.get_doc(
				{
					"doctype": "AI Agent Plugin Tool",
					"parent": agent.name,
					"parenttype": "AI Agent",
					"parentfield": "plugin_tools",
					"fac_tool": matches[0],
					"enabled": int(bool(legacy.enabled) and all(bool(row.enabled) for row in manual_configs)),
					"requires_confirmation": int(legacy.requires_confirmation),
				}
			).insert(ignore_permissions=True)
			report["migrated"].append({"agent": agent.name, "tool": tool_name, "fac_tool": matches[0]})

	frappe.db.commit()
	report["status"] = "completed" if not (report["unmatched"] or report["ambiguous"] or report["conflicts"]) else "completed_with_review"
	return report


@frappe.whitelist()
def run_ai_tool_migration() -> dict[str, Any]:
	# Rewrites tool bindings on every AI Agent, so it needs the same authority as
	# editing those agents directly. `migrate_ai_tools` itself stays uncontrolled
	# so operators can run it from `bench execute` as Administrator.
	frappe.has_permission("AI Agent", "write", throw=True)
	return migrate_ai_tools()
