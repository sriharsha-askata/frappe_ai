"""Per-site evidence gate for retiring the legacy AI Tool schema.

Retirement machinery: remove in Stage 2, once every site has migrated and the
rollback window has ended (see docs/ai-tool-retirement.md).
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frappe

LEGACY_TABLES = ("AI Tool", "AI Agent Tool", "AI Agent Tool Config")


def _rows(doctype: str, fields: list[str]) -> list[dict[str, Any]]:
	if not frappe.db.table_exists(doctype):
		return []
	return [dict(row) for row in frappe.get_all(doctype, fields=fields, order_by="name asc")]


def inventory() -> dict[str, Any]:
	"""Read all legacy definitions and bindings, including database Script code."""
	return {
		"site": frappe.local.site,
		"tools": _rows("AI Tool", ["*"]),
		"agent_tools": _rows("AI Agent Tool", ["*"]),
		"agent_tool_configs": _rows("AI Agent Tool Config", ["*"]),
		"plugin_tools": _rows("AI Agent Plugin Tool", ["*"]),
		"mcp_matches": _rows("AI MCP Tool", ["*"])
		if frappe.db.table_exists("AI MCP Tool") and frappe.db.has_column("AI MCP Tool", "matched_ai_tool") else [],
	}


def _checksum(value: Any) -> str:
	return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _archive_dir() -> Path:
	return Path(frappe.get_site_path("private", "files", "ai-tool-retirement"))


def report() -> dict[str, Any]:
	"""Return a safe operator summary without Script source or other definitions."""
	data = inventory()
	return {
		"site": data["site"],
		"tools": [
			{"slug": row["slug"] or row["name"], "type": row["type"], "enabled": bool(row["enabled"])}
			for row in data["tools"]
		],
		"legacy_bindings": [
			{"agent": row["parent"], "tool": row["tool"]} for row in data["agent_tools"]
		] + [
			{"agent": row["parent"], "tool": row["tool_name"], "source": row["source"], "enabled": bool(row["enabled"])}
			for row in data["agent_tool_configs"]
		],
		"plugin_bindings": [
			{"agent": row["parent"], "tool": row["fac_tool"], "enabled": bool(row["enabled"])}
			for row in data["plugin_tools"]
		],
	}


def archive(dispositions: dict[str, str], evidence: dict[str, dict[str, str]]) -> dict[str, str]:
	"""Save a private inventory with an owner and review reference for every slug."""
	data = inventory()
	slugs = {row["slug"] or row["name"] for row in data["tools"]}
	if set(dispositions) != slugs or set(dispositions.values()) - {"migrated", "retired"}:
		raise ValueError("Classify every AI Tool as migrated or retired, with no extra slugs")
	if set(evidence) != slugs or any(not item.get("owner") or not item.get("reference") for item in evidence.values()):
		raise ValueError("Record an owner and verification reference or retirement reason for every AI Tool")
	directory = _archive_dir()
	directory.mkdir(mode=0o700, parents=True, exist_ok=True)
	directory.chmod(0o700)
	stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
	path = directory / f"archive-{stamp}.json"
	payload = {"inventory": data, "dispositions": dispositions, "evidence": evidence, "inventory_sha256": _checksum(data)}
	fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
	with os.fdopen(fd, "w", encoding="utf-8") as output:
		json.dump(payload, output, sort_keys=True, indent=2, default=str)
	manifest = {"archive": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
	temp = directory / f"current-{stamp}.tmp"
	fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
	with os.fdopen(fd, "w", encoding="utf-8") as output:
		json.dump(manifest, output)
	os.replace(temp, directory / "current.json")
	return {"path": str(path), "sha256": manifest["sha256"]}


def assert_ready() -> None:
	"""Block schema deletion when the archive or active FAC bindings are incomplete."""
	if not any(frappe.db.table_exists(table) for table in LEGACY_TABLES):
		return
	manifest_file = _archive_dir() / "current.json"
	if not manifest_file.exists():
		raise RuntimeError("Archive AI Tool rows before migrating this release")
	manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
	path = _archive_dir() / Path(manifest["archive"]).name
	if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["sha256"]:
		raise RuntimeError("AI Tool archive checksum mismatch")
	payload = json.loads(path.read_text(encoding="utf-8"))
	data = inventory()
	if payload["inventory_sha256"] != _checksum(data):
		raise RuntimeError("AI Tool rows or bindings changed after archival")
	if frappe.db.count("AI Run", {"status": ["in", ["Running", "Paused"]]}):
		raise RuntimeError("Finish or recover active AI Runs before removing AI Tool")
	configs: dict[str, list[dict[str, Any]]] = {}
	for row in _rows("FAC Tool Configuration", ["name", "tool_name", "enabled", "module_path"]):
		configs.setdefault(row["tool_name"] or row["name"], []).append(row)
	from frappe_assistant_core.core.tool_registry import get_tool_registry

	registry = get_tool_registry()
	available = {row["name"] for row in registry.get_available_tools(user="Administrator")}
	bindings = {(row["parent"], row["fac_tool"]): row for row in data["plugin_tools"]}
	known_tools = {alias for row in data["tools"] for alias in (row["slug"], row["name"]) if alias}
	for row in data["agent_tools"]:
		if row["tool"] not in known_tools and frappe.db.exists("AI Agent", row["parent"]):
			raise RuntimeError(f"Legacy binding {row['parent']} / {row['tool']} has no AI Tool definition")
	for row in data["agent_tool_configs"]:
		if row["source"] != "mcp" and row["tool_name"] not in known_tools and frappe.db.exists("AI Agent", row["parent"]):
			raise RuntimeError(f"Legacy binding {row['parent']} / {row['tool_name']} has no AI Tool definition")
	for tool in data["tools"]:
		slug = tool["slug"] or tool["name"]
		disposition = payload["dispositions"].get(slug)
		if disposition not in {"migrated", "retired"}:
			raise RuntimeError(f"AI Tool {slug} is unclassified")
		item_evidence = payload.get("evidence", {}).get(slug) or {}
		if not item_evidence.get("owner") or not item_evidence.get("reference"):
			raise RuntimeError(f"AI Tool {slug} lacks review evidence")
		matching_configs = configs.get(slug, [])
		if disposition == "migrated" and (
			len(matching_configs) != 1 or not matching_configs[0]["enabled"]
			or not matching_configs[0]["module_path"] or slug not in available
			or not registry.get_tool(slug)
		):
			raise RuntimeError(f"AI Tool {slug} has no unique, available FAC implementation")
		aliases = {slug, tool["name"]}
		legacy = [(row["parent"], int(bool(tool["enabled"]))) for row in data["agent_tools"] if row["tool"] in aliases]
		legacy += [
			(row["parent"], int(bool(tool["enabled"] and row["enabled"])))
			for row in data["agent_tool_configs"]
			if row["tool_name"] in aliases and row["source"] != "mcp"
		]
		for agent, enabled in legacy:
			if not enabled or not frappe.db.exists("AI Agent", agent):
				continue
			if not frappe.db.get_value("AI Agent", agent, "enabled"):
				continue
			if disposition == "retired":
				raise RuntimeError(f"Active agent {agent} still binds retired AI Tool {slug}")
			if disposition == "migrated":
				config = matching_configs[0]
				binding = bindings.get((agent, config["name"]))
				if not binding or not binding["enabled"]:
					raise RuntimeError(f"AI Tool {slug} lacks an enabled FAC binding for {agent}")
				if bool(binding["requires_confirmation"]) != bool(tool["requires_confirmation"]):
					raise RuntimeError(f"AI Tool {slug} confirmation policy differs for {agent}")


def drop_retired_tables() -> None:
	"""Remove physical tables Frappe intentionally retains after orphan cleanup."""
	if not (_archive_dir() / "current.json").exists():
		# Fresh installs have no legacy data or archive.
		if any(frappe.db.table_exists(table) for table in LEGACY_TABLES):
			raise RuntimeError("Refusing to drop legacy tool tables without an archive")
		return
	for doctype in LEGACY_TABLES:
		if frappe.db.exists("DocType", doctype):
			raise RuntimeError(f"Cannot drop {doctype} while its DocType still exists")
	quote = '"' if frappe.db.db_type == "postgres" else "`"
	for doctype in LEGACY_TABLES:
		if frappe.db.table_exists(doctype):
			frappe.db.sql_ddl(f"DROP TABLE {quote}tab{doctype}{quote}")
	if frappe.db.table_exists("AI MCP Tool") and frappe.db.has_column("AI MCP Tool", "matched_ai_tool"):
		frappe.db.sql_ddl(f"ALTER TABLE {quote}tabAI MCP Tool{quote} DROP COLUMN {quote}matched_ai_tool{quote}")
