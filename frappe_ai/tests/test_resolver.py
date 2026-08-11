# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Tests for `AI Tool` (`frappe_ai/frappe_ai/doctype/ai_tool/ai_tool.py`) and
`frappe_ai/lib/resolver.py`. `IntegrationTestCase` since `AI Tool` docs and
`resolve_tool` touch the DB (Script tools run through `safe_exec`, which reads
`frappe.session.user`).
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.tests import IntegrationTestCase

from frappe_ai.lib.resolver import schema_from_code
from frappe_ai.lib.tool import Tool


def _tool(**overrides: Any) -> dict:
	doc = {
		"doctype": "AI Tool",
		"title": "Test Tool",
		"slug": "test_tool",
		"type": "Imported",
		"import_path": "frappe_ai.tools.builtins.read",
		"description": "A test tool.",
	}
	doc.update(overrides)
	return doc


class TestAIToolSlugValidation(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_valid_slug_accepted(self):
		doc = frappe.get_doc(_tool(slug="valid_slug_1")).insert()
		self.assertEqual(doc.slug, "valid_slug_1")

	def test_invalid_slugs_rejected(self):
		for bad in ("BadCase", "1starts_with_digit", "has space", "has-dash", ""):
			with self.subTest(slug=bad):
				doc = frappe.get_doc(_tool(slug=bad or "x", title=f"t-{bad}"))
				if not bad:
					doc.slug = ""
				with self.assertRaisesRegex(frappe.ValidationError, "Slug"):
					doc.insert()


class TestAIToolTypeFieldXOR(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_imported_requires_import_path(self):
		doc = frappe.get_doc(_tool(slug="no_import_path", import_path=None))
		with self.assertRaisesRegex(frappe.ValidationError, "Import Path"):
			doc.insert()

	def test_imported_rejects_inline_code(self):
		doc = frappe.get_doc(_tool(slug="both_set", code="def main():\n    return 1\n"))
		with self.assertRaisesRegex(frappe.ValidationError, "inline code"):
			doc.insert()

	def test_script_requires_code(self):
		doc = frappe.get_doc(_tool(slug="no_code", type="Script", import_path=None, code=None))
		with self.assertRaisesRegex(frappe.ValidationError, "Code"):
			doc.insert()

	def test_script_rejects_import_path(self):
		doc = frappe.get_doc(
			_tool(
				slug="script_with_path",
				type="Script",
				import_path="frappe_ai.tools.builtins.read",
				code="def main():\n    return 1\n",
			)
		)
		with self.assertRaisesRegex(frappe.ValidationError, "Import Path"):
			doc.insert()


class TestAIToolScriptValidation(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def _script_tool(self, code: str, **overrides):
		return frappe.get_doc(_tool(slug="script_tool", type="Script", import_path=None, code=code, **overrides))

	def test_valid_script_accepted(self):
		doc = self._script_tool("def main(x: int) -> int:\n    return x + 1\n")
		doc.insert()
		self.assertEqual(doc.type, "Script")

	def test_missing_main_rejected(self):
		doc = self._script_tool("def other():\n    return 1\n")
		with self.assertRaisesRegex(frappe.ValidationError, "main"):
			doc.insert()

	def test_syntax_error_rejected(self):
		doc = self._script_tool("def main(:\n    pass\n")
		with self.assertRaisesRegex(frappe.ValidationError, "syntax"):
			doc.insert()

	def test_var_args_rejected(self):
		doc = self._script_tool("def main(*args):\n    return args\n")
		with self.assertRaisesRegex(frappe.ValidationError, "args"):
			doc.insert()

	def test_var_kwargs_rejected(self):
		doc = self._script_tool("def main(**kwargs):\n    return kwargs\n")
		with self.assertRaisesRegex(frappe.ValidationError, "kwargs"):
			doc.insert()

	def test_self_call_rejected(self):
		doc = self._script_tool("def main(x: int) -> int:\n    return main(x)\n")
		with self.assertRaisesRegex(frappe.ValidationError, "main"):
			doc.insert()


class TestAIToolImmutability(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_system_generated_type_change_blocked(self):
		doc = frappe.get_doc(_tool(slug="sys_tool", is_system_generated=1)).insert(ignore_permissions=True)
		doc.type = "Script"
		doc.import_path = None
		doc.code = "def main():\n    return 1\n"
		# insert(ignore_permissions=True) leaves doc.flags.ignore_permissions set on the
		# in-memory doc, which validate_immutable's own early-return would otherwise skip —
		# clear it so this save exercises the guard, like a normal (non-migration) edit would.
		doc.flags.ignore_permissions = False
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_system_generated_flag_removal_blocked(self):
		doc = frappe.get_doc(_tool(slug="sys_tool2", is_system_generated=1)).insert(ignore_permissions=True)
		doc.is_system_generated = 0
		doc.flags.ignore_permissions = False
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_delete_blocked_for_system_generated(self):
		doc = frappe.get_doc(_tool(slug="sys_tool3", is_system_generated=1)).insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			doc.delete()


class TestSchemaFromCode(IntegrationTestCase):
	def test_primitive_params(self):
		schema = schema_from_code("def main(a: str, b: int):\n    return a\n")
		self.assertEqual(schema["properties"]["a"], {"type": "string"})
		self.assertEqual(schema["properties"]["b"], {"type": "integer"})
		self.assertEqual(set(schema["required"]), {"a", "b"})

	def test_default_makes_optional(self):
		schema = schema_from_code("def main(a: str, b: int = 5):\n    return a\n")
		self.assertEqual(schema["required"], ["a"])

	def test_literal_becomes_enum(self):
		schema = schema_from_code("def main(a: Literal['x', 'y']):\n    return a\n")
		self.assertEqual(schema["properties"]["a"], {"type": "string", "enum": ["x", "y"]})

	def test_list_becomes_array(self):
		schema = schema_from_code("def main(a: list[str]):\n    return a\n")
		self.assertEqual(schema["properties"]["a"], {"type": "array", "items": {"type": "string"}})

	def test_does_not_execute_code(self):
		# The body raises if executed; schema derivation must never run it.
		code = "def main(a: str):\n    raise RuntimeError('should not run')\n"
		schema = schema_from_code(code)
		self.assertEqual(schema["properties"]["a"], {"type": "string"})


class TestResolveTool(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_imported_tool_resolves_to_callable_tool(self):
		doc = frappe.get_doc(_tool(slug="resolve_imported")).insert()
		resolved = doc.to_tool()
		self.assertIsInstance(resolved, Tool)
		self.assertEqual(resolved.name, "resolve_imported")

	def test_script_tool_resolves_and_executes_in_sandbox(self):
		doc = frappe.get_doc(
			_tool(
				slug="resolve_script",
				type="Script",
				import_path=None,
				code="def main(x: int) -> int:\n    return x * 2\n",
			)
		).insert()
		resolved = doc.to_tool()
		self.assertEqual(resolved(x=5), 10)

	def test_bad_import_path_throws(self):
		doc = frappe.get_doc(_tool(slug="bad_import", import_path="not.a.real.module.fn"))
		# Validation only checks the dotted-path shape, not that it resolves — the
		# resolve failure surfaces at to_tool() time, matching flow's behaviour.
		doc.insert()
		with self.assertRaises(frappe.ValidationError):
			doc.to_tool()
