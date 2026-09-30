from __future__ import annotations

import importlib
import inspect

import frappe

_PATCH_SENTINEL = "_frappe_ai_decorated_plugins_patch"


def _signature_starts_with(target, expected: tuple[str, ...]) -> bool:
	return tuple(inspect.signature(target).parameters)[: len(expected)] == expected


def install_fac_compatibility() -> bool:
	"""Install the decorated-plugin adapter atomically when FAC has the expected API."""
	try:
		from frappe_assistant_core.core import tool_registry as registry_module
		from frappe_assistant_core.core.base_tool import BaseTool
		from frappe_assistant_core.utils import migration_hooks
		from frappe_assistant_core.utils import plugin_manager as manager_module
		from frappe_assistant_core.utils.plugin_manager import PluginInfo, PluginState, ToolInfo
	except ImportError:
		return False

	discovery_class = manager_module.PluginDiscovery
	manager_class = manager_module.PluginManager
	persistence_class = manager_module.PluginPersistence
	registry_class = registry_module.ToolRegistry
	targets = {
		"discover_plugins": (discovery_class.discover_plugins, ("self",)),
		"_load_plugin_tools": (
			manager_class._load_plugin_tools,
			("self", "plugin_name", "plugin_info"),
		),
		"_load_tools": (manager_class._load_tools, ("self",)),
		"load_enabled_plugins": (persistence_class.load_enabled_plugins, ("self",)),
		"_get_external_tools": (registry_class._get_external_tools, ("self",)),
		"_sync_tool_configurations": (migration_hooks._sync_tool_configurations, ()),
	}
	patched = [getattr(target, _PATCH_SENTINEL, False) for target, _expected in targets.values()]
	if any(patched):
		if all(patched):
			return True
		frappe.logger("frappe_ai.tool_discovery").error(
			"FAC decorated-plugin adapter is only partially installed; decorated plugins are disabled"
		)
		return False
	incompatible = [
		name
		for name, (target, expected) in targets.items()
		if not _signature_starts_with(target, expected)
	]
	if incompatible:
		frappe.logger("frappe_ai.tool_discovery").error(
			"FAC decorated-plugin adapter is incompatible with: " + ", ".join(incompatible)
		)
		return False

	original_discover = discovery_class.discover_plugins
	original_load_plugin = manager_class._load_plugin_tools
	original_enabled = persistence_class.load_enabled_plugins
	original_external = registry_class._get_external_tools
	original_sync = migration_hooks._sync_tool_configurations

	def discover_plugins(self):
		plugins = original_discover(self)
		from frappe_ai.assistant_tools.discovery import load_registered_plugin_modules
		from frappe_ai.assistant_tools.registry import get_decorated_plugins

		load_registered_plugin_modules()
		for name, spec in get_decorated_plugins().items():
			if name in plugins and not getattr(plugins[name], "_frappe_ai_provider_path", None):
				raise ValueError(f"Decorated plugin {name!r} collides with a native FAC plugin")
			info = PluginInfo(
				name=name,
				display_name=spec.display_name,
				description=spec.description,
				version=spec.version,
				state=PluginState.DISCOVERED,
				tools=[item.name for item in spec.tools],
			)
			info._frappe_ai_tool_classes = spec.tool_classes
			info._frappe_ai_provider_path = spec.provider_path
			info._frappe_ai_dependencies = spec.dependencies
			plugins[name] = info
		return plugins

	def load_plugin_tools(self, plugin_name, plugin_info):
		tool_classes = getattr(plugin_info, "_frappe_ai_tool_classes", None)
		if tool_classes is None:
			return original_load_plugin(self, plugin_name, plugin_info)
		for dependency in getattr(plugin_info, "_frappe_ai_dependencies", ()):
			importlib.import_module(dependency)
		tools = {}
		for tool_class in tool_classes:
			if not isinstance(tool_class, type) or not issubclass(tool_class, BaseTool):
				raise TypeError(f"{tool_class!r} is not a BaseTool subclass")
			instance = tool_class()
			valid, error = instance.validate_dependencies()
			if not valid:
				raise RuntimeError(error)
			if instance.name in tools:
				raise ValueError(f"Plugin {plugin_name!r} contains duplicate tool {instance.name!r}")
			tools[instance.name] = ToolInfo(
				name=instance.name,
				plugin_name=plugin_name,
				description=instance.description,
				instance=instance,
			)
		return tools

	def load_tools(self):
		loaded = {}
		providers = {}
		plugin_names = sorted(
			self._enabled_plugins,
			key=lambda name: bool(
				getattr(self._discovered_plugins.get(name), "_frappe_ai_provider_path", None)
			),
		)
		for plugin_name in plugin_names:
			plugin_info = self._discovered_plugins.get(plugin_name)
			if not plugin_info or plugin_info.state == PluginState.ERROR:
				continue
			try:
				candidate = self._load_plugin_tools(plugin_name, plugin_info)
				collisions = sorted(set(candidate).intersection(loaded))
				if collisions:
					raise ValueError(
						f"Plugin {plugin_name!r} collides with {providers[collisions[0]]!r} "
						f"on tools: {', '.join(collisions)}"
					)
				loaded.update(candidate)
				providers.update(dict.fromkeys(candidate, plugin_name))
			except Exception as exc:
				plugin_info.state = PluginState.ERROR
				plugin_info.error_message = str(exc)
				self.logger.error(f"Failed to load tools for plugin {plugin_name!r}: {exc}")
		self._loaded_tools = loaded

	def load_enabled_plugins(self):
		enabled = set(original_enabled(self))
		from frappe_ai.assistant_tools.discovery import load_registered_plugin_modules
		from frappe_ai.assistant_tools.registry import get_decorated_plugins

		load_registered_plugin_modules()
		for name, spec in get_decorated_plugins().items():
			if frappe.db.table_exists("FAC Plugin Configuration") and frappe.db.exists(
				"FAC Plugin Configuration", name
			):
				is_enabled = bool(frappe.db.get_value("FAC Plugin Configuration", name, "enabled"))
			else:
				is_enabled = spec.enabled_by_default
			if is_enabled:
				enabled.add(name)
			else:
				enabled.discard(name)
		return enabled

	def get_external_tools(self):
		tools = original_external(self)
		from frappe_ai.assistant_tools.discovery import discover_standalone_tools

		for name, info in discover_standalone_tools().items():
			if name in tools and tools[name].instance.__class__ is not info.instance.__class__:
				raise ValueError(
					f"Automatically discovered tool {name!r} collides with an assistant_tools hook"
				)
			tools[name] = info
		return tools

	def sync_tool_configurations():
		result = original_sync()
		from frappe_ai.assistant_tools.sync import sync_decorated_tools

		sync_decorated_tools()
		return result

	wrappers = (
		discover_plugins,
		load_plugin_tools,
		load_tools,
		load_enabled_plugins,
		get_external_tools,
		sync_tool_configurations,
	)
	for wrapper in wrappers:
		setattr(wrapper, _PATCH_SENTINEL, True)

	discovery_class.discover_plugins = discover_plugins
	manager_class._load_plugin_tools = load_plugin_tools
	manager_class._load_tools = load_tools
	persistence_class.load_enabled_plugins = load_enabled_plugins
	registry_class._get_external_tools = get_external_tools
	migration_hooks._sync_tool_configurations = sync_tool_configurations
	manager_module._plugin_manager = None
	registry_module._tool_registry = None
	frappe.logger("frappe_ai.tool_discovery").info("Installed decorated FAC plugin compatibility")
	return True


def ensure_fac_compatibility(*_args, **_kwargs) -> None:
	install_fac_compatibility()
