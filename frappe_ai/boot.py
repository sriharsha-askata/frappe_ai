# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from frappe_ai.knowledge.extract import FILE_EXTENSIONS


def boot_session(bootinfo):
	bootinfo.frappe_ai_supported_file_types = sorted(FILE_EXTENSIONS)
