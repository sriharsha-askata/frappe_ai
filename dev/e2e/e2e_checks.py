# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Checks run inside a bench site: `bench --site <site> execute e2e_checks.<name>`.

Each function prints one `CHECK <name>: PASS|FAIL <detail>` line per check and returns a dict, so
the runner can read results from the output. Keep them free of side effects beyond what they create.
"""

from __future__ import annotations

import json

import frappe


def _report(name: str, ok: bool, detail: str = "") -> dict:
	print(f"CHECK {name}: {'PASS' if ok else 'FAIL'} {detail}".rstrip())
	return {"name": name, "ok": ok, "detail": detail}


def tools():
	"""The decorated tools must reach Assistant Core's registry (the adapter fails silently otherwise)."""
	from frappe_assistant_core.core.tool_registry import get_tool_registry

	frappe.get_hooks(
		"assistant_tools"
	)  # a web request loads hooks first; loading them installs the FAC adapter
	names = {t["name"] for t in get_tool_registry().get_available_tools(user="Administrator")}
	wanted = {"execute", "run_action", "search_knowledge", "update_memory"}
	return _report(
		"fac-tools-registered", wanted <= names, f"missing={sorted(wanted - names)} total={len(names)}"
	)


def install_state():
	"""Fresh install: defaults, backend resolution, no Error Log noise from this app."""
	from frappe_ai.vector.registry import configured_name

	results = [
		_report("db-type", True, frappe.db.db_type),
		_report("vector-backend-resolved", True, configured_name()),
		_report(
			"settings-defaults",
			frappe.db.get_single_value("AI Settings", "vector_backend") in (None, "", "Auto"),
			str(frappe.db.get_single_value("AI Settings", "vector_backend")),
		),
	]
	noisy = frappe.get_all(
		"Error Log", filters={"method": ["like", "%frappe_ai%"]}, fields=["method"], limit=5
	)
	results.append(_report("no-app-errors-in-log", not noisy, json.dumps(noisy)))
	return results


def fac_diagnostics():
	"""Why are (or aren't) the native tools in Assistant Core's registry?"""
	from frappe_ai.assistant_tools.fac_compat import install_fac_compatibility
	from frappe_assistant_core.core.tool_registry import get_tool_registry

	registry = get_tool_registry()
	info = {
		"hooks": frappe.get_hooks("assistant_tools"),
		"adapter_installed": install_fac_compatibility(),
	}
	try:
		info["external_tools"] = sorted(registry._get_external_tools())
	except Exception as error:
		info["external_tools_error"] = repr(error)
	info["available_before_cache_clear"] = len(registry.get_available_tools(user="Administrator"))
	frappe.cache.delete_key("assistant_tools")
	frappe.clear_cache()
	registry = get_tool_registry()
	names = sorted(t["name"] for t in registry.get_available_tools(user="Administrator"))
	info["available_after_cache_clear"] = len(names)
	info["ours"] = [n for n in names if n in ("execute", "run_action", "search_knowledge", "update_memory")]
	print("DIAG " + json.dumps(info, default=str))
	return info


def fac_adapter_state():
	"""Is the Assistant Core adapter installed after hooks load, without calling it ourselves?"""
	frappe.get_hooks("assistant_tools")
	from frappe_assistant_core.core import tool_registry as registry_module

	patched = bool(
		getattr(registry_module.ToolRegistry._get_external_tools, "_frappe_ai_decorated_plugins_patch", False)
	)
	print("DIAG adapter_patched_after_hooks=" + str(patched))
	return {"patched": patched}


def pgvector_through_frappe():
	"""The pgvector backend running on Frappe's own database layer (`frappe.db.sql`)."""
	from frappe_ai.vector.base import Item
	from frappe_ai.vector.pgvector_backend import PgVectorBackend
	from frappe_ai.vector.registry import FrappeSqlRunner

	if frappe.db.db_type != "postgres":
		return _report("pgvector-through-frappe", True, "skipped (not postgres)")
	backend = PgVectorBackend(FrappeSqlRunner())
	name = "e2e_probe"
	backend.drop(name)
	backend.ensure_collection(name, dimension=4, identity={"k": 1}, filterable=["kb"])
	backend.upsert(
		name,
		[
			Item("1", "printer setup guide", [1, 0, 0, 0], {"kb": "A"}),
			Item("2", "laptop onboarding", [0, 1, 0, 0], {"kb": "B"}),
		],
	)
	vec = backend.search(name, vector=[1, 0, 0, 0], limit=2)
	hybrid = backend.search(name, vector=[0, 1, 0, 0], text="printer", where={"kb": ["A", "B"]}, limit=2)
	filtered = backend.search(name, vector=[1, 0, 0, 0], where={"kb": "B"})
	backend.delete(name, ids=["1"])
	left = backend.list_ids(name)
	backend.drop(name)
	ok = (
		[h.id for h in vec] == ["1", "2"]
		and len(hybrid) == 2
		and [h.id for h in filtered] == ["2"]
		and left == {"2"}
	)
	return _report(
		"pgvector-through-frappe",
		ok,
		f"vec={[h.id for h in vec]} hybrid={[h.id for h in hybrid]} filtered={[h.id for h in filtered]} left={sorted(left)}",
	)


def count_syntax():
	"""Which aggregate syntax does this Frappe accept in `fields`, on this database?"""
	out = {}
	for label, kwargs in {
		"dict-as": {"fields": [{"COUNT": "*", "as": "total"}]},
		"dict-as order_by ''": {"fields": [{"COUNT": "*", "as": "total"}], "order_by": ""},
		"dict-as order_by None": {"fields": [{"COUNT": "*", "as": "total"}], "order_by": None},
	}.items():
		try:
			out[label] = frappe.get_list("ToDo", **kwargs)[:1]
		except Exception as error:
			out[label] = "ERR " + str(error)[:70]
		frappe.db.rollback()
	print("DIAG " + json.dumps(out, default=str))
	return out
