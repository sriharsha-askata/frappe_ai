# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Run `module.function` inside a bench site, as Administrator.

    python run_in_site.py <site> <module.function> [json-kwargs]

Run it with the bench virtualenv's Python from the bench's `sites` directory. The function's
return value is printed as JSON on a line starting `RESULT `; the transaction is committed
unless the function raises.
"""

from __future__ import annotations

import importlib
import json
import sys

import frappe


def main() -> int:
	site, target = sys.argv[1], sys.argv[2]
	kwargs = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
	module_name, _, function_name = target.rpartition(".")
	frappe.init(site=site, sites_path=".")
	frappe.connect()
	frappe.set_user("Administrator")
	for method in frappe.get_hooks("before_job"):  # what a background worker does before running a job
		frappe.get_attr(method)()
	try:
		result = getattr(importlib.import_module(module_name), function_name)(**kwargs)
		frappe.db.commit()
		print("RESULT " + json.dumps(result, default=str))
		return 0
	except Exception:
		frappe.db.rollback()
		raise
	finally:
		frappe.destroy()


if __name__ == "__main__":
	sys.exit(main())
