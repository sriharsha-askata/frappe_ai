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
	from frappe_assistant_core.core.tool_registry import get_tool_registry

	from frappe_ai.assistant_tools.fac_compat import install_fac_compatibility

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
	_upsert(
		"AI Provider", "openai", {"provider": "openai", "enabled": 1, "base_url": STUB_URL, "api_key": "stub"}
	)
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
		frappe.get_doc({"doctype": "AI Knowledge Base", "title": "E2E KB", "enabled": 1}).insert(
			ignore_permissions=True
		)
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
		{
			"doctype": "AI Knowledge Source",
			"title": title,
			"knowledge_base": kb,
			"source_type": "Text",
			"content": content,
		}
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
	return frappe.get_all(
		"AI Agent Memory", filters={"agent": agent}, fields=["name", "scope", "content", "status"]
	)


# --- more helpers: backends, memory, triggers, attachments -------------------------------


def set_backend(name: str):
	"""Select a vector backend the way an admin would (validated on save), and drop cached backends."""
	from frappe_ai.vector import registry

	doc = frappe.get_doc("AI Settings")
	doc.vector_backend = name
	doc.save(ignore_permissions=True)
	registry.reset_cache()
	return {"configured": registry.configured_name()}


def rebuild_index():
	from frappe_ai.knowledge.reindex import rebuild_knowledge_index

	return rebuild_knowledge_index()


def retrieve(query: str, kb: str, limit: int = 5):
	from frappe_ai.knowledge.retriever import retrieve as run

	try:
		hits = run(query, kbs=[kb], limit=limit)
		return {"ok": True, "contents": [h["content"][:60] for h in hits], "count": len(hits)}
	except frappe.ValidationError as error:
		return {"ok": False, "error": str(error)[:160]}


def reset_knowledge(kb: str):
	"""Remove a knowledge base's sources/chunks so a scenario starts clean (index included)."""
	from frappe_ai.knowledge import index
	from frappe_ai.knowledge.ingest import purge_source

	for source in frappe.get_all("AI Knowledge Source", filters={"knowledge_base": kb}, pluck="name"):
		purge_source(source)
		frappe.db.delete("AI Knowledge Source", {"name": source})
	index.chunks.drop()
	return True


def add_memories(agent: str, count: int, special_word: str):
	"""`count` filler memories plus one containing `special_word`; returns the special memory's text."""
	for i in range(count):
		frappe.get_doc(
			{
				"doctype": "AI Agent Memory",
				"agent": agent,
				"scope": "Agent",
				"content": f"filler fact number {i} about warehouse logistics",
				"status": "Active",
			}
		).insert(ignore_permissions=True)
	text = f"the {special_word} account is managed by Priya"
	frappe.get_doc(
		{"doctype": "AI Agent Memory", "agent": agent, "scope": "Agent", "content": text, "status": "Active"}
	).insert(ignore_permissions=True)
	return {"special": text}


def clear_memories(agent: str):
	frappe.db.delete("AI Agent Memory", {"agent": agent})
	return True


def make_trigger(agent: str, kind: str):
	"""A DocType-event trigger on ToDo, or a scheduled trigger that is already due."""
	title = f"E2E {kind}"
	frappe.db.delete("AI Trigger", {"title": title})
	values = {
		"doctype": "AI Trigger",
		"title": title,
		"enabled": 1,
		"agent": agent,
		"run_as": "Administrator",
		"prompt_template": "Check {{ doc.name if doc else 'scheduled' }} [[search: refund window]]",
	}
	if kind == "event":
		values.update(event="DocType Event", target_doctype="ToDo", doc_event="after_insert")
	else:
		values.update(event="Scheduled", cron_expression="* * * * *", last_fired_at="2020-01-01 00:00:00")
	doc = frappe.get_doc(values).insert(ignore_permissions=True)
	return {"name": doc.name}


def fire_due_scheduled():
	from frappe_ai.triggers import triggers

	triggers.dispatch_scheduled()
	return True


def insert_todo(description: str):
	todo = frappe.get_doc({"doctype": "ToDo", "description": description}).insert(ignore_permissions=True)
	return {"name": todo.name}


def trigger_runs(trigger: str):
	return frappe.get_all(
		"AI Run", filters={"trigger": trigger}, fields=["name", "status", "error"], order_by="creation asc"
	)


def claim_window(trigger: str, start_at: float):
	"""Try to claim the trigger's due window; used by two processes at once."""
	import time

	from frappe_ai.triggers.triggers import _claim_scheduled_window

	last = frappe.db.get_value("AI Trigger", trigger, "last_fired_at")
	time.sleep(max(0.0, start_at - time.time()))
	won = _claim_scheduled_window(trigger, last, frappe.utils.now_datetime())
	frappe.db.commit()
	return {"won": bool(won), "last": str(last)}


def error_log_tail(since_minutes: int = 30):
	rows = frappe.get_all(
		"Error Log",
		filters={
			"creation": [">", frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-since_minutes)]
		},
		fields=["method"],
		limit=50,
	)
	return [r["method"] for r in rows if r.get("method")]


def attachment_index_count():
	from frappe_ai.knowledge import index

	return {"exists": index.attachments.exists(), "count": index.attachments.count()}


def delete_session(session: str):
	frappe.delete_doc("AI Session", session, ignore_permissions=True, force=True)
	return True


def session_titles(prefix: str):
	for title in (prefix + " 100%_match", prefix + " plain other"):
		if not frappe.db.exists("AI Session", {"title": title}):
			frappe.get_doc(
				{
					"doctype": "AI Session",
					"agent": frappe.db.get_value("AI Agent", {"title": "E2E Agent"}, "name"),
					"title": title,
				}
			).insert(ignore_permissions=True)
	return True


def trigger_run_details(title: str = "E2E scheduled"):
	trigger = frappe.db.get_value(
		"AI Trigger", {"title": title}, ["name", "last_fired_at", "creation"], as_dict=True
	)
	runs = frappe.get_all(
		"AI Run",
		filters={"trigger": trigger.name},
		fields=["name", "status", "creation", "source"],
		order_by="creation asc",
	)
	return {"trigger": trigger, "runs": runs}


def ingest_failure_after_index_write(kb: str):
	"""Fail an ingestion after the index write: does the index keep ids the database rolled back?"""
	from unittest.mock import patch

	from frappe_ai.knowledge import index
	from frappe_ai.knowledge.ingest import ingest_source

	frappe.flags.skip_auto_ingest = True
	source = frappe.get_doc(
		{
			"doctype": "AI Knowledge Source",
			"title": "Fail after index",
			"knowledge_base": kb,
			"source_type": "Text",
			"content": "alpha beta gamma delta " * 10,
		}
	).insert(ignore_permissions=True)
	frappe.flags.skip_auto_ingest = False
	frappe.db.commit()
	real_add = index.chunks.add

	def add_then_fail(rows):
		real_add(rows)
		raise RuntimeError("injected failure right after the index write")

	with patch.object(index.chunks, "add", side_effect=add_then_fail):
		try:
			ingest_source(source.name)
		except RuntimeError:
			pass
	db_ids = {int(n) for n in frappe.get_all("AI Knowledge Chunk", pluck="name")}
	idx_ids = index.chunks.ids() if index.chunks.exists() else set()
	orphans = sorted(idx_ids - db_ids)
	status = frappe.db.get_value("AI Knowledge Source", source.name, "status")
	return {
		"backend": index.chunks.backend().name,
		"status": status,
		"orphan_index_ids": len(orphans),
		"missing_from_index": len(db_ids - idx_ids),
	}


def pgvector_extension_missing_message():
	"""With the extension absent, creating an index must fail with a clear, actionable message."""
	from frappe_ai.knowledge import index

	try:
		index.chunks.ensure(4)
		return {"raised": False}
	except frappe.ValidationError as error:
		return {"raised": True, "message": str(error)[:200]}
