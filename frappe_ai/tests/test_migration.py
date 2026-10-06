"""The preparatory migration must read the agent's actual legacy child table."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from frappe_ai.api.migration import migrate_ai_tools


class TestLegacyToolMigration(unittest.TestCase):
	def test_config_table_binding_is_migrated_once(self):
		rows = {
			"FAC Tool Configuration": [SimpleNamespace(name="read", tool_name="read")],
			"AI Tool": [SimpleNamespace(name="read", slug="read", enabled=1, requires_confirmation=0)],
			"AI Agent": [SimpleNamespace(name="Agent")],
			"AI Agent Tool": [],
			"AI Agent Tool Config": [SimpleNamespace(tool_name="read", source="manual", enabled=1)],
		}
		new_binding = Mock()
		with (
			patch("frappe_ai.api.migration.frappe.db.exists", return_value=True),
			patch("frappe_ai.api.migration.frappe.db.table_exists", return_value=True),
			patch("frappe_ai.api.migration.frappe.db.get_value", side_effect=[None, SimpleNamespace(enabled=1, requires_confirmation=0)]),
			patch("frappe_ai.api.migration.frappe.db.commit"),
			patch("frappe_ai.api.migration.frappe.get_all", side_effect=lambda doctype, **_kwargs: rows[doctype]),
			patch("frappe_ai.api.migration.frappe.get_doc", return_value=new_binding) as get_doc,
			patch("frappe_assistant_core.utils.migration_hooks._sync_tool_configurations"),
		):
			first = migrate_ai_tools()
			second = migrate_ai_tools()

		self.assertEqual(first["status"], "completed")
		self.assertEqual(first["migrated"], [{"agent": "Agent", "tool": "read", "fac_tool": "read"}])
		self.assertEqual(second["skipped"], [{"agent": "Agent", "tool": "read"}])
		get_doc.assert_called_once()
		new_binding.insert.assert_called_once_with(ignore_permissions=True)
