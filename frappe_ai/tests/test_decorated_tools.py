from __future__ import annotations

from typing import Annotated

from frappe.tests import UnitTestCase

from frappe_ai.assistant_tools import plugin, tool
from frappe_ai.assistant_tools.fac_compat import install_fac_compatibility
from frappe_ai.assistant_tools.registry import get_decorated_plugins, restore_decorated_registry


class TestDecoratedTools(UnitTestCase):
	def setUp(self):
		self._registry_snapshot = get_decorated_plugins()

	def tearDown(self):
		restore_decorated_registry(self._registry_snapshot)

	def test_plugin_infers_metadata_and_compiles_tools(self):
		@plugin
		class ExampleTools:
			"""Example operations."""

			@tool.read_only
			def lookup(self, name: Annotated[str, "Record name"], limit: int = 20) -> dict:
				"""Look up one record."""
				return {"name": name, "limit": limit}

		spec = get_decorated_plugins()["example_tools"]
		self.assertEqual(spec.display_name, "Example Tools")
		self.assertEqual(spec.description, "Example operations.")
		self.assertEqual(len(spec.tools), 1)

		tool_class = spec.tool_classes[0]
		instance = tool_class()
		self.assertEqual(instance.name, "lookup")
		self.assertEqual(instance.category, "read_only")
		self.assertEqual(instance.inputSchema["required"], ["name"])
		self.assertEqual(instance.inputSchema["properties"]["name"]["description"], "Record name")
		self.assertEqual(instance.execute({"name": "SO-1", "limit": "3"}), {"name": "SO-1", "limit": 3})

	def test_normalizer_runs_before_validation_and_hidden_args_are_not_exposed(self):
		calls = []

		def normalize(arguments):
			calls.append(dict(arguments))
			arguments["count"] = int(arguments["count"])

		@plugin(name="normalizer_tools")
		class NormalizerTools:
			"""Normalization tools."""

			@tool.write(normalize_arguments=normalize)
			def save(self, count: int, __frappe_ai_run: str | None = None) -> dict:
				"""Save a normalized count."""
				return {"count": count, "run": __frappe_ai_run}

		instance = get_decorated_plugins()["normalizer_tools"].tool_classes[0]()
		self.assertNotIn("__frappe_ai_run", instance.inputSchema["properties"])
		result = instance._safe_execute({"count": "4", "__frappe_ai_run": "RUN-1"})
		self.assertTrue(result["success"])
		self.assertEqual(result["result"], {"count": 4, "run": "RUN-1"})
		self.assertEqual(len(calls), 1)

	def test_explicit_schema_is_preserved(self):
		schema = {
			"type": "object",
			"additionalProperties": False,
			"required": ["code"],
			"properties": {"code": {"type": "string", "maxLength": 8_000}},
		}

		@plugin(name="cad_tools")
		class CadTools:
			"""CAD operations."""

			@tool.privileged(input_schema=schema)
			def execute_code(self, code: str) -> dict:
				"""Execute restricted CAD code."""
				return {"code": code}

		spec = get_decorated_plugins()["cad_tools"]
		self.assertEqual(spec.tool_classes[0].__name__, "ExecuteCodeTool")
		self.assertEqual(spec.tool_classes[0]().inputSchema, schema)

	def test_invalid_tool_definition_fails_at_import_time(self):
		with self.assertRaisesRegex(TypeError, "type annotation"):

			@plugin(name="invalid_tools")
			class InvalidTools:
				"""Invalid operations."""

				@tool.read_only
				def invalid(self, value):
					"""Missing annotation."""
					return value

	def test_fac_discovers_and_loads_named_plugin(self):
		@plugin(name="fac_discovery_fixture", display_name="FAC Discovery Fixture")
		class FACDiscoveryFixture:
			"""Fixture plugin for FAC discovery."""

			@tool.read_only
			def ping(self, value: str) -> dict:
				"""Return a fixture value."""
				return {"value": value}

		self.assertTrue(install_fac_compatibility())

		from frappe_assistant_core.utils.plugin_manager import PluginDiscovery, PluginManager

		info = PluginDiscovery().discover_plugins()["fac_discovery_fixture"]
		manager = PluginManager.__new__(PluginManager)
		loaded = manager._load_plugin_tools("fac_discovery_fixture", info)
		self.assertEqual(set(loaded), {"ping"})
		self.assertEqual(loaded["ping"].instance.execute({"value": "ok"}), {"value": "ok"})
