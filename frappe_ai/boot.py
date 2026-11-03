# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from frappe_ai.knowledge.extract import supported_extensions


def boot_session(bootinfo):
	bootinfo.frappe_ai_supported_file_types = sorted(supported_extensions())
