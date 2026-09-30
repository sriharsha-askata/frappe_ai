"""TEMPORARY ONE-TIME MIGRATION — TO BE REMOVED once every site has been migrated.

To remove: delete this file and its line in `frappe_ai/patches.txt`.

Bring existing `AI MCP Connection` rows to the structured field format.

- stdio: `command` becomes the executable alone, `command_args` a JSON list of strings.
- `environment_variables`: scalar values are stringified (subprocess environments are strings).
- `api_key`: was a plain `Data` column, is now a `Password`; existing plaintext values are
  moved into encrypted storage.
- `mcp_config`: import-only, cleared (it can hold secrets and duplicates the fields above).

Rows that cannot be normalized automatically are left untouched and listed at the end.
"""

from __future__ import annotations

import json
import sys

import frappe
from frappe.utils.password import set_encrypted_password

from frappe_ai.frappe_ai.doctype.ai_mcp_connection.ai_mcp_connection import is_masked, split_stdio_command

DOCTYPE = "AI MCP Connection"


def _stringify_env(raw: str | None) -> str | None:
	"""Return normalized env JSON, or None when the value is missing, invalid, or unchanged."""
	if not raw:
		return None
	try:
		value = json.loads(raw)
	except ValueError:
		return None
	if not isinstance(value, dict):
		return None
	converted = {}
	for key, item in value.items():
		if isinstance(item, bool):
			item = "true" if item else "false"
		elif isinstance(item, int | float):
			item = str(item)
		if not isinstance(item, str):
			return None
		converted[key] = item
	normalized = json.dumps(converted)
	return normalized if normalized != raw else None


def execute() -> dict:
	if not frappe.db.exists("DocType", DOCTYPE):
		return {}

	needs_attention: list[tuple[str, str]] = []
	changed = 0
	fields = ["name", "connection_type", "command", "command_args", "environment_variables", "api_key", "mcp_config"]
	for row in frappe.get_all(DOCTYPE, fields=fields):
		updates: dict = {}

		if row.connection_type == "stdio":
			try:
				executable, args = split_stdio_command(row.command, row.command_args)
			except ValueError as e:
				needs_attention.append((row.name, str(e)))
			else:
				if executable and (executable != row.command or json.dumps(args) != row.command_args):
					updates.update(command=executable, command_args=json.dumps(args))

		env = _stringify_env(row.environment_variables)
		if env is not None:
			updates["environment_variables"] = env

		if row.api_key and not is_masked(row.api_key):
			set_encrypted_password(DOCTYPE, row.name, row.api_key, "api_key")
			updates["api_key"] = "*" * len(row.api_key)

		if row.mcp_config:
			updates["mcp_config"] = None

		if updates:
			frappe.db.set_value(DOCTYPE, row.name, updates, update_modified=False)
			changed += 1

	if needs_attention:
		print(
			"\nACTION REQUIRED: these AI MCP Connection rows need a manual fix "
			"(open, correct Command / Command Arguments, save):",
			file=sys.stderr,
		)
		for name, reason in needs_attention:
			print(f"  {name}: {reason}", file=sys.stderr)
	return {"updated": changed, "needs_attention": [name for name, _reason in needs_attention]}
