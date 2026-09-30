from __future__ import annotations

import json

import frappe

from frappe_ai.assistant_tools.discovery import discover_standalone_tools, load_registered_plugin_modules
from frappe_ai.assistant_tools.fac_compat import install_fac_compatibility
from frappe_ai.assistant_tools.registry import get_decorated_plugins


def sync_decorated_tools() -> dict:
	"""Reconcile decorated plugins/tools into FAC configuration without replacing admin policy."""
	install_fac_compatibility()
	load_registered_plugin_modules()
	plugins = get_decorated_plugins()
	standalone = discover_standalone_tools(force=True)
	result = {"plugins_created": [], "plugins_updated": [], "tools_created": [], "tools_updated": []}

	if not frappe.db.table_exists("FAC Plugin Configuration") or not frappe.db.table_exists(
		"FAC Tool Configuration"
	):
		result["skipped"] = "FAC configuration DocTypes are not installed"
		return result

	enabled_plugins = _legacy_enabled_plugins()
	for plugin_name, spec in plugins.items():
		existing = frappe.db.exists("FAC Plugin Configuration", plugin_name)
		if existing:
			doc = frappe.get_doc("FAC Plugin Configuration", plugin_name)
			doc.display_name = spec.display_name
			doc.description = spec.description
			doc.save(ignore_permissions=True)
			result["plugins_updated"].append(plugin_name)
		else:
			doc = frappe.get_doc(
				{
					"doctype": "FAC Plugin Configuration",
					"plugin_name": plugin_name,
					"display_name": spec.display_name,
					"description": spec.description,
					"enabled": int(spec.enabled_by_default),
					"discovered_at": frappe.utils.now(),
				}
			).insert(ignore_permissions=True)
			result["plugins_created"].append(plugin_name)
		if doc.enabled:
			enabled_plugins.add(plugin_name)
		else:
			enabled_plugins.discard(plugin_name)

		for tool_class in spec.tool_classes:
			_sync_tool_config(tool_class(), plugin_name, result)

	for info in standalone.values():
		_sync_tool_config(info.instance, "custom_tools", result)

	_write_legacy_enabled_plugins(enabled_plugins)
	frappe.cache.delete_value("fac_tool_registry_configs")

	try:
		from frappe_assistant_core.utils import plugin_manager as manager_module
		from frappe_assistant_core.core import tool_registry as registry_module

		manager_module._plugin_manager = None
		registry_module._tool_registry = None
	except ImportError:
		pass
	return result


def _sync_tool_config(instance, plugin_name: str, result: dict) -> None:
	tool_name = instance.name
	existing = frappe.db.exists("FAC Tool Configuration", tool_name)
	module_path = f"{instance.__class__.__module__}.{instance.__class__.__name__}"
	category = instance.category
	if category not in {"read_only", "write", "read_write", "privileged"}:
		try:
			from frappe_assistant_core.utils.tool_category_detector import detect_tool_category

			category = detect_tool_category(instance)
		except Exception:
			category = "read_write"
	if category == "dangerous":
		category = "privileged"
	if existing:
		# Provider metadata may change during deployment, but the effective stored
		# category is administrator policy. Bypass the DocType controller here:
		# its validate() copies auto_detected_category into tool_category whenever
		# category_override is false, which would rewrite existing-site behavior.
		frappe.db.set_value(
			"FAC Tool Configuration",
			tool_name,
			{
				"plugin_name": plugin_name,
				"description": instance.description or "",
				"auto_detected_category": category,
				"source_app": instance.source_app,
				"module_path": module_path,
			},
			update_modified=False,
		)
		result["tools_updated"].append(tool_name)
		return

	frappe.get_doc(
		{
			"doctype": "FAC Tool Configuration",
			"tool_name": tool_name,
			"plugin_name": plugin_name,
			"description": instance.description or "",
			"enabled": int(getattr(instance.__class__, "_frappe_ai_spec", None).enabled_by_default)
			if getattr(instance.__class__, "_frappe_ai_spec", None)
			else 1,
			"tool_category": category,
			"auto_detected_category": category,
			"category_override": 0,
			"role_access_mode": "Allow All",
			"source_app": instance.source_app,
			"module_path": module_path,
		}
	).insert(ignore_permissions=True)
	result["tools_created"].append(tool_name)


def _legacy_enabled_plugins() -> set[str]:
	try:
		value = frappe.db.get_single_value("Assistant Core Settings", "enabled_plugins_list")
		return set(json.loads(value)) if value else set()
	except Exception:
		return set()


def _write_legacy_enabled_plugins(enabled: set[str]) -> None:
	settings = frappe.get_doc("Assistant Core Settings")
	settings.enabled_plugins_list = json.dumps(sorted(enabled))
	settings.save(ignore_permissions=True)
