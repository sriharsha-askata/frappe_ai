# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

import json
import re
from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.model.document import Document

from frappe_ai.lib.model import (
	ModelConfigurationError,
	is_known_provider,
	resolve_model_config,
	to_litellm_provider,
)
from frappe_ai.frappe_ai.doctype.ai_model.connection_test import (
	CHAT_CHECKS,
	blocked_suite,
	run_capability_suite,
)

# Bare model id — no `provider/` prefix. The provider slug is identity/endpoint
# metadata; execution always uses the shared OpenAI-compatible transport.
MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-:.\/]*$")
EMBEDDING_ID_MARKERS = ("embedding", "embed-", "embed.")

RESERVED_PARAM_KEYS = frozenset(
	{"model", "api_key", "api_base", "base_url", "messages", "stream", "tools", "tool_choice"}
)


class AIModel(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		api_key: DF.Password | None
		base_url: DF.Data | None
		context_window: DF.Int
		enabled: DF.Check
		is_default: DF.Check
		model_id: DF.Data
		params: DF.JSON | None
		provider: DF.Link | None
		title: DF.Data
	# end: auto-generated types

	def validate(self):
		self._normalize()
		self._validate_model_id()
		self._validate_base_url()
		self._validate_params()
		self._enforce_single_default()

	def after_insert(self):
		if not self.enabled:
			return
		try:
			from frappe_ai.assistant import sync_builtin_assistant
		except ImportError:
			# frappe_ai.assistant doesn't exist until Phase 7; no-op until then.
			return

		sync_builtin_assistant(model=self.name)

	def _enforce_single_default(self):
		if not self.is_default:
			return
		# Only one AI Model may be the default — clear is_default on all others.
		frappe.db.set_value(
			"AI Model",
			{"is_default": 1, "name": ("!=", self.name)},
			"is_default",
			0,
			update_modified=False,
		)

	def _normalize(self):
		for field in ("title", "model_id", "base_url"):
			value = self.get(field)
			if isinstance(value, str):
				self.set(field, value.strip())
		if isinstance(self.api_key, str):
			self.api_key = self.api_key.strip()

	def _validate_model_id(self):
		model_id = self.model_id or ""
		if not MODEL_ID_PATTERN.match(model_id):
			frappe.throw(
				_(
					"Model ID must be a bare model identifier (e.g. <code>claude-sonnet-4-6</code>), not a <code>provider/model</code> string — the provider comes from the linked Provider. Only alphanumerics, dashes, dots, colons or slashes are allowed."
				),
				title=_("Invalid Model ID"),
			)
		if any(marker in model_id.casefold() for marker in EMBEDDING_ID_MARKERS):
			frappe.throw(
				_("AI Model only supports chat models. Embeddings use the fixed Ollama nomic-embed-text model."),
				title=_("Invalid Chat Model"),
			)

	def _validate_base_url(self):
		if not self.base_url:
			return
		parsed = urlparse(self.base_url)
		if parsed.scheme not in ("http", "https") or not parsed.netloc:
			frappe.throw(
				_("Base URL must be an absolute http(s) URL."),
				title=_("Invalid Base URL"),
			)

	def _validate_params(self):
		if not self.params:
			return
		try:
			parsed = json.loads(self.params)
		except (TypeError, ValueError):
			frappe.throw(_("Params must be valid JSON."), title=_("Invalid Params"))
		if not isinstance(parsed, dict):
			frappe.throw(_("Params must be a JSON object."), title=_("Invalid Params"))
		conflicting = sorted(RESERVED_PARAM_KEYS.intersection(parsed))
		if conflicting:
			frappe.throw(
				_("Params may not include reserved keys: {0}.").format(", ".join(conflicting)),
				title=_("Reserved Params"),
			)

	@frappe.whitelist()
	def test_connection(self):
		"""Run a fresh capability suite for this saved model configuration.

		This is intentionally configuration-time only. Runtime agent execution is
		constructed by ``AgentBuilder`` and never calls this method.
		"""
		self.check_permission("write")
		try:
			model_config = resolve_model_config(self)
		except ModelConfigurationError as e:
			return blocked_suite(CHAT_CHECKS, e, provider=self.provider, model_id=self.model_id)
		except Exception as e:
			return blocked_suite(CHAT_CHECKS, e, provider=self.provider, model_id=self.model_id)

		return run_capability_suite(model_config)


@frappe.whitelist()
def get_provider_models(provider: str | None = None) -> list[str]:
	"""Return LiteLLM model-id suggestions for a provider identity.

	Suggestions are UX-only; execution always uses the shared OpenAI-compatible
	transport and accepts a manually entered model id as well.
	"""
	if not provider or not is_known_provider(provider):
		return []

	import litellm

	return sorted(
		model
		for model in litellm.models_by_provider.get(to_litellm_provider(provider.strip().lower()), set())
		if not any(marker in model.casefold() for marker in EMBEDDING_ID_MARKERS)
	)
