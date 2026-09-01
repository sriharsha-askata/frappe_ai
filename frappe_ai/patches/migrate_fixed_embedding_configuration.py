"""Prepare legacy embedding configuration for the fixed Ollama index."""

import sys

from frappe_ai.knowledge.migration import migrate_legacy_embedding_configuration


def execute():
	result = migrate_legacy_embedding_configuration()
	if result.get("status") == "legacy_configuration_found":
		print(
			"\nACTION REQUIRED: Knowledge store rebuild needed.\n"
			f"  Legacy embedding model: {result.get('legacy_model')}\n"
			"  Knowledge search is disabled until you run:\n"
			"    bench execute frappe_ai.knowledge.rebuild_knowledge_index\n",
			file=sys.stderr,
		)
	return result
