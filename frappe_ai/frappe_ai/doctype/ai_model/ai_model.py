# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

import json
import re
from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.model.document import Document

from frappe_ai.lib.model import get_model_class, is_known_provider

# Bare model id — no `provider/` prefix. The Agno model class comes from the linked
# Provider's slug, not from the id string. See ADR 0009.
MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-:.\/]*$")

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
		if not MODEL_ID_PATTERN.match(self.model_id or ""):
			frappe.throw(
				_(
					"Model ID must be a bare model identifier (e.g. <code>claude-sonnet-4-6</code>), not a <code>provider/model</code> string — the provider comes from the linked Provider. Only alphanumerics, dashes, dots, colons or slashes are allowed."
				),
				title=_("Invalid Model ID"),
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
		self.check_permission("write")

		# Two-state credential split, mirroring `_model_call_config` (api/service.py):
		# a linked Provider supplies the class + credentials outright; unlinked falls
		# back to this model's own fields against the OpenAI-compatible Agno class.
		if self.provider:
			provider_doc = frappe.get_doc("AI Provider", self.provider)
			try:
				model_cls = get_model_class(provider_doc.provider)
			except ImportError as e:
				frappe.throw(str(e), title=_("Missing Dependency"))
			api_key = provider_doc.get_password("api_key", raise_exception=False) or None
			base_url = provider_doc.base_url
		else:
			from agno.models.openai import OpenAIChat as model_cls

			api_key = self.get_password("api_key", raise_exception=False) or None
			base_url = self.base_url

		kwargs = {"id": self.model_id, "api_key": api_key, "timeout": 15}
		if base_url:
			kwargs["base_url"] = base_url

		try:
			from agno.models.message import Message

			model = model_cls(**kwargs)
			model.response(messages=[Message(role="user", content="ping")])
		except Exception as e:
			frappe.throw(str(e)[:500] or type(e).__name__, title=_(type(e).__name__))

		return {"ok": True, "message": _("Connection OK")}


@frappe.whitelist()
def get_provider_models(provider: str | None = None) -> list[str]:
	"""Model-id suggestions for `provider` (an `AI Provider` docname, an Agno/
	`PROVIDER_MODEL_CLASSES` slug). Hints only — `AI Model.model_id` accepts free
	text regardless (see ai_model.js). Two-factor filter: litellm must know the
	provider's model list (keyed by litellm's own spelling — see
	`to_litellm_provider` for the handful of slugs where Agno and litellm disagree),
	and frappe_ai/Agno must actually be able to run that provider slug
	(`is_known_provider`) — a provider litellm recognizes but Agno has no model class
	for yields no suggestions."""
	if not provider or not is_known_provider(provider):
		return []

	import litellm

	from frappe_ai.lib.model import to_litellm_provider

	return sorted(litellm.models_by_provider.get(to_litellm_provider(provider.strip().lower()), set()))
