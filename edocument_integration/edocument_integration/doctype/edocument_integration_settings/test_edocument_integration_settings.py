# Copyright (c) 2025, Prilk Consulting BV and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = ["Company", "EDocument Profile"]


class IntegrationTestEDocumentIntegrationSettings(IntegrationTestCase):
	"""
	Integration tests for EDocumentIntegrationSettings.
	Use this class for testing interactions between multiple components.
	"""

	def test_already_imported_document_is_skipped_silently(self):
		reference = "TEST-INBOX-DOC-0001"
		frappe.get_doc({"doctype": "EDocument", "reference": reference, "direction": "Incoming"}).insert(
			ignore_permissions=True
		)

		error_logs_before = frappe.db.count("Error Log")

		result = frappe.new_doc("EDocument Integration Settings").process_incoming_document(
			b"<Invoice/>", reference
		)

		self.assertTrue(result["skipped"])
		self.assertEqual(result["reason"], "duplicate")
		self.assertEqual(
			frappe.db.count("Error Log"),
			error_logs_before,
			"Skipping an already imported document must not write an Error Log",
		)
