from __future__ import annotations

import copy
import inspect
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from frappe_assistant_core.core.base_tool import BaseTool

from frappe_ai.lib.tool import Tool as RuntimeTool
from frappe_ai.lib.tool import build_schema

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_CATEGORIES = frozenset({"read_only", "write", "read_write", "privileged"})


def _is_hidden_parameter(name: str) -> bool:
	return name.startswith("__frappe_ai_") or "__frappe_ai_" in name


def _public_hidden_name(name: str) -> str:
	if name.startswith("__frappe_ai_"):
		return name
	return name[name.index("__frappe_ai_") :]


def _snake_case(value: str) -> str:
	value = re.sub(r"(?<!^)(?=[A-Z])", "_", value).replace("-", "_").replace(" ", "_")
	return re.sub(r"_+", "_", value).strip("_").lower()


@dataclass(frozen=True)
class ToolSpec:
	func: Callable[..., Any]
	name: str
	description: str
	category: str
	dependencies: tuple[str, ...] = ()
	default_config: dict[str, Any] = field(default_factory=dict)
	input_schema: dict[str, Any] = field(default_factory=dict)
	normalize_arguments: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None
	enabled_by_default: bool = True


@dataclass(frozen=True)
class PluginSpec:
	name: str
	display_name: str
	description: str
	version: str
	author: str
	dependencies: tuple[str, ...]
	enabled_by_default: bool
	requires_restart: bool
	source_app: str
	provider_path: str
	tools: tuple[ToolSpec, ...]
	tool_classes: tuple[type[BaseTool], ...]


_DECORATED_PLUGINS: dict[str, PluginSpec] = {}


def get_decorated_plugins() -> dict[str, PluginSpec]:
	return dict(_DECORATED_PLUGINS)


def clear_decorated_registry() -> None:
	_DECORATED_PLUGINS.clear()


def restore_decorated_registry(snapshot: dict[str, PluginSpec]) -> None:
	"""Restore a captured registry after a scoped test without losing imported providers."""
	_DECORATED_PLUGINS.clear()
	_DECORATED_PLUGINS.update(snapshot)


def _validate_tool_function(func: Callable[..., Any], name: str, description: str) -> None:
	if inspect.iscoroutinefunction(func):
		raise TypeError(f"Tool {name!r} must be synchronous")
	if not _NAME_RE.fullmatch(name):
		raise ValueError(f"Tool name {name!r} must use lowercase snake_case")
	if not description:
		raise ValueError(f"Tool {name!r} requires a description or docstring")

	for parameter in inspect.signature(func).parameters.values():
		if parameter.name in {"self", "cls"} or _is_hidden_parameter(parameter.name):
			continue
		if parameter.kind in {parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD}:
			raise TypeError(f"Tool {name!r} cannot use *args or **kwargs")
		if parameter.annotation is inspect.Parameter.empty:
			raise TypeError(f"Tool {name!r} parameter {parameter.name!r} requires a type annotation")


def _tool_schema(func: Callable[..., Any], explicit: dict[str, Any] | None) -> dict[str, Any]:
	if explicit is not None:
		if explicit.get("type") != "object" or not isinstance(explicit.get("properties", {}), dict):
			raise ValueError("input_schema must be a JSON Schema object")
		return copy.deepcopy(explicit)

	schema = build_schema(func)
	properties = schema.get("properties", {})
	hidden = {name for name in properties if _is_hidden_parameter(name)}
	for name in hidden:
		properties.pop(name, None)
	if schema.get("required"):
		schema["required"] = [name for name in schema["required"] if name not in hidden]
		if not schema["required"]:
			schema.pop("required")
	return schema


class _ToolDecorator:
	def __init__(self, category: str):
		self.category = category

	def __call__(
		self,
		func: Callable[..., Any] | None = None,
		*,
		name: str | None = None,
		description: str | None = None,
		dependencies: tuple[str, ...] = (),
		default_config: dict[str, Any] | None = None,
		input_schema: dict[str, Any] | None = None,
		normalize_arguments: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
		enabled_by_default: bool = True,
	):
		def decorate(target: Callable[..., Any]) -> ToolSpec:
			tool_name = name or target.__name__
			tool_description = (description or inspect.getdoc(target) or "").strip()
			_validate_tool_function(target, tool_name, tool_description)
			return ToolSpec(
				func=target,
				name=tool_name,
				description=tool_description,
				category=self.category,
				dependencies=tuple(dependencies),
				default_config=copy.deepcopy(default_config or {}),
				input_schema=_tool_schema(target, input_schema),
				normalize_arguments=normalize_arguments,
				enabled_by_default=bool(enabled_by_default),
			)

		return decorate(func) if func is not None else decorate


class ToolDecorators:
	read_only = _ToolDecorator("read_only")
	write = _ToolDecorator("write")
	read_write = _ToolDecorator("read_write")
	privileged = _ToolDecorator("privileged")


tool = ToolDecorators()


def compile_tool(
	spec: ToolSpec, owner_class: type | None, plugin_name: str, source_app: str
) -> type[BaseTool]:
	class_name = "".join(part.title() for part in spec.name.split("_")) + "Tool"

	class GeneratedTool(BaseTool):
		_frappe_ai_generated = True
		_frappe_ai_plugin_name = plugin_name
		_frappe_ai_spec = spec

		def __init__(self):
			super().__init__()
			self.name = spec.name
			self.description = spec.description
			self.inputSchema = copy.deepcopy(spec.input_schema)
			self.category = spec.category
			self.source_app = source_app
			self.dependencies = list(spec.dependencies)
			self.default_config = copy.deepcopy(spec.default_config)
			if owner_class is None:
				bound = spec.func
			else:
				owner = owner_class()
				bound = spec.func.__get__(owner, owner_class)
			self._hidden_parameter_names = {
				_public_hidden_name(parameter.name): parameter.name
				for parameter in inspect.signature(spec.func).parameters.values()
				if _is_hidden_parameter(parameter.name)
			}
			self._runtime_tool = RuntimeTool(
				name=spec.name,
				description=spec.description,
				parameters=spec.input_schema,
				func=bound,
			)

		def execute(self, arguments: dict[str, Any]) -> Any:
			values = self._prepare_arguments(arguments, normalize=False)
			return self._runtime_tool(**values)

		def validate_arguments(self, arguments: dict[str, Any]) -> None:
			values = self._prepare_arguments(arguments, normalize=True)
			arguments.clear()
			arguments.update(values)
			super().validate_arguments(arguments)

		def _prepare_arguments(
			self, arguments: dict[str, Any], *, normalize: bool
		) -> dict[str, Any]:
			values = dict(arguments or {})
			for public_name, actual_name in self._hidden_parameter_names.items():
				if public_name in values and actual_name != public_name:
					values[actual_name] = values.pop(public_name)
			if normalize and spec.normalize_arguments:
				normalized = spec.normalize_arguments(values)
				if normalized is not None:
					values = normalized
			return values

	GeneratedTool.__name__ = class_name
	GeneratedTool.__qualname__ = class_name
	GeneratedTool.__module__ = spec.func.__module__
	return GeneratedTool


def plugin(
	cls: type | None = None,
	*,
	name: str | None = None,
	display_name: str | None = None,
	description: str | None = None,
	version: str = "1.0.0",
	author: str = "",
	dependencies: tuple[str, ...] = (),
	enabled_by_default: bool = True,
	requires_restart: bool = False,
	tools: tuple[type[BaseTool], ...] = (),
):
	def decorate(owner_class: type) -> type:
		plugin_name = name or _snake_case(owner_class.__name__)
		if not _NAME_RE.fullmatch(plugin_name):
			raise ValueError(f"Plugin name {plugin_name!r} must use lowercase snake_case")
		plugin_description = (description or inspect.getdoc(owner_class) or "").strip()
		if not plugin_description:
			raise ValueError(f"Plugin {plugin_name!r} requires a description or class docstring")
		if plugin_name in _DECORATED_PLUGINS:
			raise ValueError(f"Plugin {plugin_name!r} is already registered")

		tool_specs = tuple(value for value in vars(owner_class).values() if isinstance(value, ToolSpec))
		names = [item.name for item in tool_specs]
		if len(names) != len(set(names)):
			raise ValueError(f"Plugin {plugin_name!r} contains duplicate tool names")
		source_app = owner_class.__module__.split(".", 1)[0]
		compiled = tuple(compile_tool(item, owner_class, plugin_name, source_app) for item in tool_specs)
		compiled = (*compiled, *tools)
		module = sys.modules.get(owner_class.__module__)
		if module:
			for tool_class in compiled:
				existing = getattr(module, tool_class.__name__, None)
				compatible_generated = (
					existing is not None
					and getattr(existing, "_frappe_ai_generated", False)
					and getattr(existing, "_frappe_ai_plugin_name", None) == plugin_name
					and getattr(getattr(existing, "_frappe_ai_spec", None), "name", None)
					== getattr(tool_class._frappe_ai_spec, "name", None)
				)
				if existing is not None and existing is not tool_class and not compatible_generated:
					raise ValueError(
						f"Cannot export compatibility alias {tool_class.__name__!r}; "
						f"{owner_class.__module__} already defines it"
					)
				setattr(module, tool_class.__name__, tool_class)

		spec = PluginSpec(
			name=plugin_name,
			display_name=display_name or plugin_name.replace("_", " ").title(),
			description=plugin_description,
			version=version,
			author=author,
			dependencies=tuple(dependencies),
			enabled_by_default=bool(enabled_by_default),
			requires_restart=bool(requires_restart),
			source_app=source_app,
			provider_path=f"{owner_class.__module__}.{owner_class.__name__}",
			tools=tool_specs,
			tool_classes=tuple(compiled),
		)
		_DECORATED_PLUGINS[plugin_name] = spec
		owner_class._frappe_ai_plugin_spec = spec
		return owner_class

	return decorate(cls) if cls is not None else decorate
