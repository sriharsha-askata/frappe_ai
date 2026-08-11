# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Tests for `frappe_ai/lib/tool.py` — pure Python (no Frappe context needed), so
plain `unittest.TestCase` per this app's test-split convention (see
`test_service_auth.py`'s module docstring for the rationale).
"""

from __future__ import annotations

import unittest
from enum import Enum
from typing import Annotated, Literal, Optional

from pydantic import BaseModel

from frappe_ai.lib.tool import Tool, build_schema, tool


class Color(Enum):
	"""Module-level (not nested in a test method) — `get_type_hints` resolves
	annotations against the function's `__globals__`, so a class defined inside a
	test method is invisible to it and raises `NameError` at schema-build time."""

	RED = "red"
	BLUE = "blue"


class Address(BaseModel):
	"""Module-level for the same reason as `Color` above."""

	city: str
	zip_code: str


class TestToolDecorator(unittest.TestCase):
	def test_infers_name_and_description_from_function(self):
		@tool
		def greet(name: str) -> str:
			"""Say hello."""
			return f"hello {name}"

		self.assertEqual(greet.name, "greet")
		self.assertEqual(greet.description, "Say hello.")
		self.assertFalse(greet.requires_confirmation)

	def test_explicit_overrides(self):
		@tool(name="custom", description="Custom description", requires_confirmation=True)
		def fn(x: int) -> int:
			return x

		self.assertEqual(fn.name, "custom")
		self.assertEqual(fn.description, "Custom description")
		self.assertTrue(fn.requires_confirmation)

	def test_call_invokes_wrapped_function(self):
		@tool
		def add(a: int, b: int) -> int:
			return a + b

		self.assertEqual(add(a=1, b=2), 3)

	def test_to_dict_shape(self):
		@tool
		def fn(x: int) -> int:
			"""Doc."""
			return x

		d = fn.to_dict()
		self.assertEqual(d["type"], "function")
		self.assertEqual(d["function"]["name"], "fn")
		self.assertEqual(d["function"]["description"], "Doc.")
		self.assertIn("parameters", d["function"])


class TestBuildSchemaPrimitives(unittest.TestCase):
	def test_primitive_types(self):
		def fn(a: str, b: int, c: float, d: bool):
			pass

		schema = build_schema(fn)
		self.assertEqual(schema["properties"]["a"], {"type": "string"})
		self.assertEqual(schema["properties"]["b"], {"type": "integer"})
		self.assertEqual(schema["properties"]["c"], {"type": "number"})
		self.assertEqual(schema["properties"]["d"], {"type": "boolean"})
		self.assertEqual(set(schema["required"]), {"a", "b", "c", "d"})

	def test_default_makes_param_optional(self):
		def fn(a: str, b: int = 5):
			pass

		schema = build_schema(fn)
		self.assertEqual(schema["required"], ["a"])

	def test_self_and_cls_excluded(self):
		def fn(self, cls, a: str):
			pass

		schema = build_schema(fn)
		self.assertEqual(set(schema["properties"].keys()), {"a"})

	def test_var_args_excluded(self):
		def fn(a: str, *args, **kwargs):
			pass

		schema = build_schema(fn)
		self.assertEqual(set(schema["properties"].keys()), {"a"})


class TestBuildSchemaContainers(unittest.TestCase):
	def test_list_type(self):
		def fn(a: list[str]):
			pass

		schema = build_schema(fn)
		self.assertEqual(schema["properties"]["a"], {"type": "array", "items": {"type": "string"}})

	def test_dict_type(self):
		def fn(a: dict):
			pass

		schema = build_schema(fn)
		self.assertEqual(schema["properties"]["a"], {"type": "object"})

	def test_bare_list_type(self):
		def fn(a: list):
			pass

		schema = build_schema(fn)
		self.assertEqual(schema["properties"]["a"], {"type": "array"})


class TestBuildSchemaOptionalAndUnion(unittest.TestCase):
	def test_optional_not_required(self):
		def fn(a: Optional[str]):
			pass

		schema = build_schema(fn)
		self.assertEqual(schema["properties"]["a"], {"type": "string"})
		self.assertNotIn("required", schema)

	def test_pipe_optional_not_required(self):
		def fn(a: str | None):
			pass

		schema = build_schema(fn)
		self.assertNotIn("required", schema)

	def test_union_becomes_anyof(self):
		def fn(a: int | str):
			pass

		schema = build_schema(fn)
		self.assertIn("anyOf", schema["properties"]["a"])


class TestBuildSchemaLiteralAndEnum(unittest.TestCase):
	def test_literal_string_enum(self):
		def fn(a: Literal["x", "y"]):
			pass

		schema = build_schema(fn)
		self.assertEqual(schema["properties"]["a"], {"type": "string", "enum": ["x", "y"]})

	def test_python_enum(self):
		def fn(a: Color):
			pass

		schema = build_schema(fn)
		self.assertEqual(schema["properties"]["a"]["enum"], ["red", "blue"])


class TestBuildSchemaAnnotatedAndPydantic(unittest.TestCase):
	def test_annotated_description(self):
		def fn(a: Annotated[str, "the name"]):
			pass

		schema = build_schema(fn)
		self.assertEqual(schema["properties"]["a"]["description"], "the name")
		self.assertEqual(schema["properties"]["a"]["type"], "string")

	def test_pydantic_model_inlined(self):
		def fn(a: Address):
			pass

		schema = build_schema(fn)
		self.assertEqual(schema["properties"]["a"]["type"], "object")
		self.assertIn("city", schema["properties"]["a"]["properties"])


if __name__ == "__main__":
	unittest.main()
