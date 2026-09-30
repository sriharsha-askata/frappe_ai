from __future__ import annotations

from typing import Annotated

from frappe.tests import UnitTestCase

from frappe_ai.assistant_tools import plugin, tool
from frappe_ai.assistant_tools.discovery import clear_discovery_cache, discover_standalone_tools
from frappe_ai.assistant_tools.fac_compat import install_fac_compatibility
from frappe_ai.assistant_tools.registry import get_decorated_plugins, restore_decorated_registry


class TestToolDiscovery(UnitTestCase):
	def setUp(self):
		self._registry_snapshot = get_decorated_plugins()

	def tearDown(self):
		restore_decorated_registry(self._registry_snapshot)
		clear_discovery_cache()

	def test_native_assistant_tools_are_discovered_as_standalone(self):
		discovered = discover_standalone_tools(force=True)
		self.assertIn("execute", discovered)
		self.assertIn("search_knowledge", discovered)
		self.assertEqual(discovered["execute"].plugin_name, "custom_tools")

	def test_plugin_owned_tools_are_not_registered_as_standalone(self):
		@plugin(name="owned_tools_fixture")
		class OwnedToolsFixture:
			"""Owned tools."""

			@tool.read_only
			def owned_ping(self, value: str) -> dict:
				"""Return a fixture value."""
				return {"value": value}

		discovered = discover_standalone_tools(force=True)
		self.assertNotIn("owned_ping", discovered)

	def test_fac_adapter_merges_decorated_plugins(self):
		@plugin(name="fac_merge_fixture", display_name="FAC Merge Fixture")
		class FACMergeFixture:
			"""Fixture plugin."""

			@tool.read_only
			def merge_ping(self, value: Annotated[str, "Fixture value"]) -> dict:
				"""Return a fixture value."""
				return {"value": value}

		self.assertTrue(install_fac_compatibility())
		from frappe_assistant_core.utils.plugin_manager import PluginDiscovery

		plugins = PluginDiscovery().discover_plugins()
		self.assertIn("fac_merge_fixture", plugins)
		self.assertIn("merge_ping", plugins["fac_merge_fixture"].tools)
