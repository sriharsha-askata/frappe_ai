app_name = "frappe_ai"
app_title = "Frappe AI"
app_publisher = "Asakta"
app_description = "frappe ai"
app_email = "askata@asakta.com"
app_license = "mit"

import os as _os


def _frappe_ai_panel_asset(filename: str) -> str:
	path = _os.path.join(_os.path.dirname(__file__), "public", "frappe_ai_panel", filename)
	try:
		version = int(_os.path.getmtime(path))
	except OSError:
		version = 0
	return f"/assets/frappe_ai/frappe_ai_panel/{filename}?v={version}"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "frappe_ai",
# 		"logo": "/assets/frappe_ai/logo.png",
# 		"title": "Frappe AI",
# 		"route": "/frappe_ai",
# 		"has_permission": "frappe_ai.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# Esbuild-built desk panel bundle, served directly from public/ with mtime-based
# cache busting so rebuilds invalidate the browser without changing filenames.
app_include_js = [_frappe_ai_panel_asset("frappe_ai_panel.js")]
app_include_css = [_frappe_ai_panel_asset("frappe_ai_panel.css")]

assistant_tools = [
	"frappe_ai.assistant_tools.native.ExecuteTool",
	"frappe_ai.assistant_tools.native.RunActionTool",
	"frappe_ai.assistant_tools.native.SearchKnowledgeTool",
	"frappe_ai.assistant_tools.native.UpdateMemoryTool",
]

# include js, css files in header of web template
# web_include_css = "/assets/frappe_ai/css/frappe_ai.css"
# web_include_js = "/assets/frappe_ai/js/frappe_ai.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "frappe_ai/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "frappe_ai/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "frappe_ai.utils.jinja_methods",
# 	"filters": "frappe_ai.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "frappe_ai.install.before_install"
# after_install = "frappe_ai.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "frappe_ai.uninstall.before_uninstall"
# after_uninstall = "frappe_ai.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "frappe_ai.utils.before_app_install"
# after_app_install = "frappe_ai.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "frappe_ai.utils.before_app_uninstall"
# after_app_uninstall = "frappe_ai.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "frappe_ai.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"*": {
		"after_insert": "frappe_ai.triggers.dispatch",
		"on_update": "frappe_ai.triggers.dispatch",
		"on_submit": "frappe_ai.triggers.dispatch",
		"on_cancel": "frappe_ai.triggers.dispatch",
		"on_trash": "frappe_ai.triggers.dispatch",
	}
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"daily": [
		# Incremental DocType-source sync — see 002-feature-mapping.md §5.22.
		"frappe_ai.knowledge.ingest.sync_due_sources",
	],
	"cron": {
		"*/5 * * * *": [
			"frappe_ai.triggers.dispatch_scheduled",
			"frappe_ai.api.mcp.check_all_mcp_connections",
		],
	},
}

# Testing
# -------

# before_tests = "frappe_ai.install.before_tests"

# Migration
# ---------
# Upserts the 10 builtin tools as system-generated AI Tool rows on every migrate.
# Phase 7's sync_builtin_assistant will also call this (see AI Model.after_insert's
# ImportError guard); it runs independently here so builtins exist before Phase 7 lands.
after_migrate = [
	"frappe_ai.tools.builtins.sync_builtin_tools",
	"frappe_ai.api.fac_tools.sync_fac_tools",
	"frappe_ai.api.migration.migrate_ai_tools",
]

extend_bootinfo = "frappe_ai.boot.boot_session"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "frappe_ai.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "frappe_ai.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "frappe_ai.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# A deleted DocType-source row's chunk is expected to disappear on the next
# incremental sweep / reconcile — link-checking must not block that delete instead.
ignore_links_on_delete = ["AI Knowledge Chunk", "AI Run", "AI Session"]

# Request Events
# ----------------
# before_request = ["frappe_ai.utils.before_request"]
# after_request = ["frappe_ai.utils.after_request"]

# Job Events
# ----------
# before_job = ["frappe_ai.utils.before_job"]
# after_job = ["frappe_ai.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"frappe_ai.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }
