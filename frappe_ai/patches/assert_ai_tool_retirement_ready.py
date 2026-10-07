"""Block legacy schema removal until the site's archive and FAC bindings are verified.

Retirement machinery: remove in Stage 2 with its patches.txt entry.
"""

from frappe_ai.api.retirement import assert_ready


def execute():
	assert_ready()
