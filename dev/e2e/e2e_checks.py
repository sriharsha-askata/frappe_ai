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


# --- seeding and inspection used by run_scenarios.py ---------------------------------------

STUB_URL = "http://127.0.0.1:11434/v1"
KB_TEXT = (
	"Refund policy. Customers may return any product within 30 days of purchase for a full refund. "
	"Refunds are issued to the original payment method within five business days. "
	"Shipping policy. Orders ship within two working days and arrive in three to five days. "
	"Warranty. Hardware carries a one year warranty covering manufacturing defects."
)


def _upsert(doctype: str, name: str, values: dict) -> None:
	if frappe.db.exists(doctype, name):
		doc = frappe.get_doc(doctype, name)
		doc.update(values)
		doc.save(ignore_permissions=True)
	else:
		frappe.get_doc({"doctype": doctype, "name": name, **values}).insert(ignore_permissions=True)


def seed():
	"""Create the provider, model, agent, knowledge base and API keys the scenarios use."""
	_upsert("AI Provider", "openai", {"provider": "openai", "enabled": 1, "base_url": STUB_URL, "api_key": "stub"})
	if not frappe.db.exists("AI Model", {"title": "E2E Model"}):
		frappe.get_doc(
			{
				"doctype": "AI Model",
				"title": "E2E Model",
				"enabled": 1,
				"provider": "openai",
				"model_id": "stub-model",
				"context_window": 8000,
			}
		).insert(ignore_permissions=True)
	model = frappe.db.get_value("AI Model", {"title": "E2E Model"}, "name")
	if not frappe.db.exists("AI Knowledge Base", "E2E KB"):
		frappe.get_doc({"doctype": "AI Knowledge Base", "title": "E2E KB", "enabled": 1}).insert(ignore_permissions=True)
	kb = frappe.db.get_value("AI Knowledge Base", {"title": "E2E KB"}, "name")
	if not frappe.db.exists("AI Agent", {"title": "E2E Agent"}):
		frappe.get_doc(
			{
				"doctype": "AI Agent",
				"title": "E2E Agent",
				"enabled": 1,
				"model": model,
				"instructions": "You are a test assistant.",
				"markdown": 0,
				"knowledge_bases": [{"knowledge_base": kb}],
				"plugin_tools": [
					{"fac_tool": "search_knowledge", "enabled": 1, "requires_confirmation": 0},
					{"fac_tool": "update_memory", "enabled": 1, "requires_confirmation": 1},
				],
			}
		).insert(ignore_permissions=True)
	agent = frappe.db.get_value("AI Agent", {"title": "E2E Agent"}, "name")
	user = frappe.get_doc("User", "Administrator")
	if not user.api_key:
		user.api_key = frappe.generate_hash(length=15)
	secret = frappe.generate_hash(length=15)
	user.api_secret = secret
	user.save(ignore_permissions=True)
	return {"agent": agent, "model": model, "kb": kb, "token": f"{user.api_key}:{secret}"}


def add_text_source(kb: str, title: str = "Policies", content: str = KB_TEXT):
	"""Create a text source (ingestion is queued after commit) and return its name."""
	doc = frappe.get_doc(
		{"doctype": "AI Knowledge Source", "title": title, "knowledge_base": kb, "source_type": "Text", "content": content}
	).insert(ignore_permissions=True)
	return doc.name


def source_state(name: str):
	doc = frappe.get_doc("AI Knowledge Source", name)
	return {"status": doc.status, "chunk_count": doc.chunk_count, "error": doc.error_log}


def index_consistency():
	"""Chunk rows in the database must match the ids in the search index exactly."""
	from frappe_ai.knowledge import index

	db_ids = {int(n) for n in frappe.get_all("AI Knowledge Chunk", pluck="name")}
	idx_ids = index.chunks.ids() if index.chunks.exists() else set()
	return {"db": len(db_ids), "index": len(idx_ids), "equal": db_ids == idx_ids}


def run_state(run: str):
	doc = frappe.get_doc("AI Run", run)
	return {
		"status": doc.status,
		"error": doc.error,
		"iterations": doc.iterations,
		"usage": frappe.parse_json(doc.usage) if doc.usage else None,
		"approvals": frappe.parse_json(doc.approvals) if doc.approvals else {},
		"questions": frappe.parse_json(doc.questions) if doc.questions else None,
		"budget": frappe.parse_json(doc.budget_usage) if doc.budget_usage else None,
	}


def memories(agent: str):
	return frappe.get_all("AI Agent Memory", filters={"agent": agent}, fields=["name", "scope", "content", "status"])
