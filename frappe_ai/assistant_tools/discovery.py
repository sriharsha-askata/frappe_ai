from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import frappe
from frappe_assistant_core.core.base_tool import BaseTool
from frappe_assistant_core.utils.plugin_manager import ToolInfo

from frappe_ai.assistant_tools.registry import ToolSpec, compile_tool, get_decorated_plugins

_standalone_cache: dict[str, ToolInfo] | None = None


def load_registered_plugin_modules() -> None:
	errors = []
	for provider_path in frappe.get_hooks("frappe_ai_tool_plugins") or []:
		try:
			module_path, attribute = provider_path.rsplit(".", 1)
			provider = getattr(importlib.import_module(module_path), attribute)
			if not getattr(provider, "_frappe_ai_plugin_spec", None):
				raise TypeError(f"{provider_path} is not decorated with @plugin")
		except Exception as exc:
			errors.append(f"{provider_path}: {exc}")
	if errors:
		raise RuntimeError("Decorated plugin discovery failed: " + "; ".join(errors))


def _assistant_tool_modules(app: str) -> list[str]:
	root = Path(frappe.get_app_path(app))
	package_parent = root.parent
	modules: set[str] = set()
	for directory in sorted(path for path in root.rglob("assistant_tools") if path.is_dir()):
		if "__pycache__" in directory.parts:
			continue
		for path in sorted(directory.rglob("*.py")):
			if path.name.startswith("test_") or any(part.startswith("_") and part != "__init__.py" for part in path.parts[len(directory.parts) :]):
				continue
			relative = path.relative_to(package_parent).with_suffix("")
			parts = list(relative.parts)
			if parts[-1] == "__init__":
				parts.pop()
			if parts:
				modules.add(".".join(parts))
	return sorted(modules)


def discover_standalone_tools(*, force: bool = False) -> dict[str, ToolInfo]:
	global _standalone_cache
	if _standalone_cache is not None and not force:
		return dict(_standalone_cache)

	load_registered_plugin_modules()
	plugin_tool_specs = {
		id(tool_spec)
		for plugin_spec in get_decorated_plugins().values()
		for tool_spec in plugin_spec.tools
	}
	discovered: dict[str, ToolInfo] = {}
	providers: dict[str, str] = {}
	import_errors: list[str] = []

	for app in frappe.get_installed_apps(_ensure_on_bench=True):
		for module_path in _assistant_tool_modules(app):
			try:
				module = importlib.import_module(module_path)
			except Exception as exc:
				import_errors.append(f"{module_path}: {exc}")
				continue

			for attribute, value in vars(module).items():
				tool_class: type[BaseTool] | None = None
				if isinstance(value, ToolSpec) and id(value) not in plugin_tool_specs:
					tool_class = compile_tool(value, None, "custom_tools", app)
				elif (
					inspect.isclass(value)
					and issubclass(value, BaseTool)
					and value is not BaseTool
					and value.__module__ == module.__name__
					and not attribute.startswith("_")
					and not getattr(value, "_frappe_ai_plugin_name", None)
				):
					tool_class = value
				if tool_class is None:
					continue
				try:
					instance = tool_class()
				except Exception as exc:
					frappe.logger("frappe_ai.tool_discovery").warning(
						f"Could not instantiate {module_path}.{attribute}: {exc}"
					)
					continue
				if not instance.name:
					continue
				provider = f"{module_path}.{attribute}"
				if instance.name in discovered and providers[instance.name] != provider:
					raise ValueError(
						f"Tool {instance.name!r} is provided by both {providers[instance.name]} and {provider}"
					)
				discovered[instance.name] = ToolInfo(
					name=instance.name,
					plugin_name="custom_tools",
					description=instance.description,
					instance=instance,
				)
				providers[instance.name] = provider

	if import_errors:
		raise RuntimeError("Assistant tool module discovery failed: " + "; ".join(import_errors))

	_standalone_cache = discovered
	return dict(discovered)


def clear_discovery_cache() -> None:
	global _standalone_cache
	_standalone_cache = None


def get_discovery_summary() -> dict:
	"""Return JSON-serializable discovery output for diagnostics and CLI checks."""
	load_registered_plugin_modules()
	return {
		"plugins": sorted(get_decorated_plugins()),
		"standalone_tools": sorted(discover_standalone_tools()),
	}


def get_fac_registry_summary() -> dict:
	"""Return active FAC grouping after installing the compatibility adapter."""
	from frappe_ai.assistant_tools.fac_compat import install_fac_compatibility

	install_fac_compatibility()
	from frappe_assistant_core.core.tool_registry import get_tool_registry
	from frappe_assistant_core.utils.plugin_manager import get_plugin_manager

	manager = get_plugin_manager()
	tools = manager.get_all_tools()
	external = get_tool_registry()._get_external_tools()
	tools.update(external)
	categories: dict[str, int] = {}
	for info in tools.values():
		categories[info.plugin_name] = categories.get(info.plugin_name, 0) + 1
	return {
		"plugins": sorted(manager.get_enabled_plugins()),
		"tool_count": len(tools),
		"categories": categories,
	}
