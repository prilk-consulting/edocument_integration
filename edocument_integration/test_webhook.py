# Copyright (c) 2025, Prilk Consulting BV and Contributors
# See license.txt

import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from edocument_integration.api import webhook

DOCUMENT_ID = "doc_TEST_WEBHOOK_0001"
TEAM_ID = "team_TEST_WEBHOOK"
RECEIVED_DOCUMENT_ID = "doc_TEST_WEBHOOK_RECEIVED"

PEPPOL_INVOICE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
	xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
	xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
	<cbc:CustomizationID>urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0</cbc:CustomizationID>
	<cbc:ProfileID>urn:fdc:peppol.eu:2017:poacc:billing:01:1.0</cbc:ProfileID>
	<cbc:ID>INV-1</cbc:ID>
	<cbc:IssueDate>2026-10-01</cbc:IssueDate>
	<cbc:InvoiceTypeCode>380</cbc:InvoiceTypeCode>
	<cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
	<cac:LegalMonetaryTotal><cbc:PayableAmount currencyID="EUR">0</cbc:PayableAmount></cac:LegalMonetaryTotal>
</Invoice>"""


class TestWebhookEvents(IntegrationTestCase):
	def setUp(self):
		# The webhook commits on errors; keep everything in the test transaction
		self.enterContext(patch.object(frappe.db, "commit"))
		frappe.get_doc(
			{
				"doctype": "EDocument Integration Settings",
				"edocument_integrator": "Recommand",
				"api_key": "test-key",
				"api_secret": "test-secret",
				"account_id": TEAM_ID,
			}
		).insert(ignore_permissions=True)
		self.outgoing = frappe.get_doc(
			{"doctype": "EDocument", "direction": "Outgoing", "reference": DOCUMENT_ID}
		).insert(ignore_permissions=True)

	def _post(self, payload):
		request = Request(
			EnvironBuilder(
				method="POST", data=json.dumps(payload), content_type="application/json"
			).get_environ()
		)
		with patch.object(frappe.local, "request", request, create=True):
			return webhook()

	def _edocument_count(self):
		return frappe.db.count("EDocument")

	def _comment_exists(self):
		return frappe.db.exists(
			"Comment",
			{
				"reference_doctype": "EDocument",
				"reference_name": self.outgoing.name,
				"content": ["like", f"%{DOCUMENT_ID}%"],
			},
		)

	def test_sent_notification_comments_on_outgoing_edocument(self):
		count_before = self._edocument_count()

		response, status = self._post(
			{"eventType": "document.sent", "documentId": DOCUMENT_ID, "teamId": TEAM_ID}
		)

		self.assertEqual(status, 200)
		self.assertEqual(response["result"]["edocument"], self.outgoing.name)
		self.assertEqual(self._edocument_count(), count_before)
		self.assertTrue(self._comment_exists())

	def test_unknown_event_creates_nothing(self):
		count_before = self._edocument_count()

		response, status = self._post(
			{"eventType": "document.unknown", "documentId": DOCUMENT_ID, "teamId": TEAM_ID}
		)

		self.assertEqual(status, 200)
		self.assertIsNone(response["result"]["edocument"])
		self.assertEqual(self._edocument_count(), count_before)

	def test_unknown_team_is_rejected(self):
		count_before = self._edocument_count()

		_response, status = self._post(
			{"eventType": "document.sent", "documentId": DOCUMENT_ID, "teamId": "team_UNKNOWN"}
		)

		self.assertEqual(status, 400)
		self.assertFalse(self._comment_exists())
		self.assertEqual(self._edocument_count(), count_before)

	@patch("edocument_integration.recommand_api.get_recommand_client")
	def test_received_notification_as_guest_imports_the_document(self, get_recommand_client):
		client = MagicMock()
		client.get_document_status.return_value = {"document": {"xml": PEPPOL_INVOICE_XML}}
		get_recommand_client.return_value = client

		# Recommand calls the public webhook, so it runs as Guest
		with self.set_user("Guest"):
			_response, status = self._post(
				{"eventType": "document.received", "documentId": RECEIVED_DOCUMENT_ID, "teamId": TEAM_ID}
			)

		self.assertEqual(status, 200)
		self.assertEqual(get_recommand_client.call_args.args[0]["api_secret"], "test-secret")
		self.assertTrue(frappe.db.exists("EDocument", {"reference": RECEIVED_DOCUMENT_ID}))
