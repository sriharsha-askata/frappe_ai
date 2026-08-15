# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.model.document import Document


class AIMCPConnection(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		command: DF.Data | None
		connection_name: DF.Data
		connection_type: DF.Literal["stdio", "SSE"]
		enabled: DF.Check
		endpoint_url: DF.Data | None
		environment_variables: DF.JSON | None
		is_connected: DF.Check
		last_check_time: DF.Datetime | None
		status_message: DF.SmallText | None
	# end: auto-generated types

	def autoname(self):
		self.name = (self.connection_name or "").strip().lower().replace(" ", "-")

	def validate(self):
		self.connection_name = (self.connection_name or "").strip()
		if self.connection_type == "stdio":
			if not (self.command or "").strip():
				frappe.throw(_("Command is required for stdio connections."), title=_("Missing Command"))
			self.endpoint_url = None
		elif self.connection_type == "SSE":
			if not (self.endpoint_url or "").strip():
				frappe.throw(_("Endpoint URL is required for SSE connections."), title=_("Missing Endpoint"))
			self.command = None
		else:
			frappe.throw(_("Connection Type must be stdio or SSE."), title=_("Invalid Connection Type"))
		if self.environment_variables:
			try:
				value = json.loads(self.environment_variables) if isinstance(self.environment_variables, str) else self.environment_variables
			except (TypeError, ValueError) as e:
				frappe.throw(_("Environment Variables must be valid JSON: {0}").format(e), title=_("Invalid JSON"))
			if not isinstance(value, dict):
				frappe.throw(_("Environment Variables must be a JSON object."), title=_("Invalid JSON"))
