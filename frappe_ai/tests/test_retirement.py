"""The deletion gate must fail closed before Frappe removes the legacy schema."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from frappe_ai.api import retirement


class TestRetirementGate(unittest.TestCase):
	evidence = {"read": {"owner": "Test Owner", "reference": "test report"}}

	def setUp(self):
		self.directory = tempfile.TemporaryDirectory()
		self.addCleanup(self.directory.cleanup)
		self.inventory = {
			"site": "test.local",
			"tools": [{"name": "read", "slug": "read", "enabled": 1, "requires_confirmation": 0}],
			"agent_tools": [{"parent": "Agent", "tool": "read"}],
			"agent_tool_configs": [],
			"plugin_tools": [{"parent": "Agent", "fac_tool": "read", "enabled": 1, "requires_confirmation": 0}],
			"mcp_matches": [],
		}
		self.enterContext(patch.object(retirement, "_archive_dir", return_value=Path(self.directory.name)))
		self.enterContext(patch.object(retirement, "inventory", return_value=self.inventory))
		self.enterContext(patch.object(retirement.frappe.db, "table_exists", return_value=True))
		self.enterContext(patch.object(retirement.frappe.db, "count", return_value=0))
		self.enterContext(patch.object(retirement.frappe.db, "exists", return_value=True))
		self.enterContext(patch.object(retirement.frappe.db, "get_value", return_value=1))
		self.enterContext(patch.object(retirement, "_rows", return_value=[{"name": "read", "tool_name": "read", "enabled": 1, "module_path": "pkg.Read"}]))
		registry = Mock()
		registry.get_available_tools.return_value = [{"name": "read"}]
		self.enterContext(patch("frappe_assistant_core.core.tool_registry.get_tool_registry", return_value=registry))

	def test_archive_requires_every_slug_to_be_classified(self):
		with self.assertRaises(ValueError):
			retirement.archive({}, self.evidence)

	def test_missing_archive_blocks_deletion(self):
		with self.assertRaisesRegex(RuntimeError, "Archive AI Tool"):
			retirement.assert_ready()

	def test_stale_archive_blocks_deletion(self):
		retirement.archive({"read": "migrated"}, self.evidence)
		self.inventory["tools"][0]["slug"] = "changed"
		with self.assertRaisesRegex(RuntimeError, "changed after archival"):
			retirement.assert_ready()

	def test_missing_fac_binding_blocks_deletion(self):
		self.inventory["plugin_tools"] = []
		retirement.archive({"read": "migrated"}, self.evidence)
		with self.assertRaisesRegex(RuntimeError, "lacks an enabled FAC binding"):
			retirement.assert_ready()

	def test_migrated_binding_passes(self):
		retirement.archive({"read": "migrated"}, self.evidence)
		retirement.assert_ready()

	def test_disabled_tool_may_be_retired_while_still_listed_on_agent(self):
		self.inventory["tools"][0]["enabled"] = 0
		retirement.archive({"read": "retired"}, self.evidence)
		retirement.assert_ready()

	def test_binding_by_slug_is_checked_when_document_name_differs(self):
		self.inventory["tools"][0]["name"] = "legacy-read"
		self.inventory["plugin_tools"] = []
		retirement.archive({"read": "migrated"}, self.evidence)
		with self.assertRaisesRegex(RuntimeError, "lacks an enabled FAC binding"):
			retirement.assert_ready()
