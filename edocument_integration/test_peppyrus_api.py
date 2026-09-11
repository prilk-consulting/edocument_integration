import base64
import importlib
import json
import sys
import types
import unittest
import xml.etree.ElementTree as stdlib_etree
from unittest.mock import Mock, patch


def _install_test_stubs():
	fake_frappe = types.ModuleType("frappe")
	fake_frappe.log_error = Mock()
	fake_frappe.get_traceback = lambda: "traceback"
	sys.modules["frappe"] = fake_frappe

	fake_lxml = types.ModuleType("lxml")
	fake_lxml_etree = types.ModuleType("lxml.etree")
	fake_lxml_etree.ParseError = stdlib_etree.ParseError
	fake_lxml_etree.QName = stdlib_etree.QName
	fake_lxml_etree.fromstring = stdlib_etree.fromstring
	fake_lxml.etree = fake_lxml_etree
	sys.modules["lxml"] = fake_lxml
	sys.modules["lxml.etree"] = fake_lxml_etree

	edocument_module = types.ModuleType("edocument")
	edocument_inner_module = types.ModuleType("edocument.edocument")
	profiles_module = types.ModuleType("edocument.edocument.profiles")
	peppol_module = types.ModuleType("edocument.edocument.profiles.peppol")
	peppol_module.UBL_NAMESPACES = {
		"cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
		"cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
	}
	sys.modules["edocument"] = edocument_module
	sys.modules["edocument.edocument"] = edocument_inner_module
	sys.modules["edocument.edocument.profiles"] = profiles_module
	sys.modules["edocument.edocument.profiles.peppol"] = peppol_module


class MockResponse:
	def __init__(self, payload, status_code=200):
		self._payload = payload
		self.status_code = status_code
		self.ok = status_code < 400
		self.text = payload if isinstance(payload, str) else json.dumps(payload)
		self.content = self.text.encode("utf-8")

	def json(self):
		return self._payload


class PeppyrusAPITestCase(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		_install_test_stubs()
		cls.peppyrus_api = importlib.import_module("edocument_integration.peppyrus_api")

	def setUp(self):
		self.module = importlib.reload(self.peppyrus_api)
		self.sample_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
	xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
	xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
	<cbc:UBLVersionID>2.1</cbc:UBLVersionID>
	<cbc:CustomizationID>urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0</cbc:CustomizationID>
	<cbc:ProfileID>urn:fdc:peppol.eu:2017:poacc:billing:01:1.0</cbc:ProfileID>
	<cac:AccountingSupplierParty>
		<cac:Party>
			<cbc:EndpointID schemeID="0208">BE0123456789</cbc:EndpointID>
		</cac:Party>
	</cac:AccountingSupplierParty>
	<cac:AccountingCustomerParty>
		<cac:Party>
			<cbc:EndpointID schemeID="0088">1234567890123</cbc:EndpointID>
		</cac:Party>
	</cac:AccountingCustomerParty>
</Invoice>
"""

	def test_client_uses_documented_auth_header(self):
		client = self.module.PeppyrusAPIClient("secret-key")
		self.assertEqual(client.session.headers["X-Api-Key"], "secret-key")
		self.assertNotIn("X-PEPPYRUS-API-Key", client.session.headers)

	def test_extract_message_fields_from_ubl_invoice(self):
		fields = self.module._extract_message_fields(self.sample_xml)

		self.assertEqual(fields["sender"], "0208:BE0123456789")
		self.assertEqual(fields["recipient"], "0088:1234567890123")
		self.assertEqual(
			fields["process_type"],
			"cenbii-procid-ubl::urn:fdc:peppol.eu:2017:poacc:billing:01:1.0",
		)
		self.assertEqual(
			fields["document_type"],
			"busdox-docid-qns::urn:oasis:names:specification:ubl:schema:xsd:Invoice-2::Invoice##urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0::2.1",
		)

	def test_send_document_posts_message_body(self):
		client = self.module.PeppyrusAPIClient("secret-key")
		client._make_request = Mock(return_value=MockResponse({"id": "message-123"}))

		result = client.send_document(self.sample_xml)

		self.assertEqual(result["document_id"], "message-123")
		call_args = client._make_request.call_args.kwargs
		payload = call_args["json"]
		self.assertEqual(payload["sender"], "0208:BE0123456789")
		self.assertEqual(payload["recipient"], "0088:1234567890123")
		self.assertEqual(base64.b64decode(payload["fileContent"]).decode("utf-8"), self.sample_xml)

	def test_poll_inbox_decodes_message_content(self):
		fake_client = Mock()
		fake_client.get_inbox.return_value = {"items": [{"id": "message-1"}]}
		fake_client.get_message.return_value = {
			"id": "message-1",
			"fileContent": base64.b64encode(b"<Invoice>payload</Invoice>").decode("utf-8"),
		}

		with patch.object(self.module, "get_peppyrus_client", return_value=fake_client):
			result = self.module.poll_inbox({"api_key": "secret-key"})

		self.assertEqual(result["status"], "success")
		self.assertEqual(result["invoices"][0]["xml_bytes"], b"<Invoice>payload</Invoice>")
		self.assertEqual(result["invoices"][0]["document_id"], "message-1")

	def test_validate_connection_uses_organization_info(self):
		with patch.object(
			self.module.PeppyrusAPIClient,
			"get_organization_info",
			return_value={"name": "Example Org"},
		):
			result = self.module.validate_peppyrus_connection("secret-key")

		self.assertEqual(result["status"], "success")
		self.assertIn("Example Org", result["message"])


if __name__ == "__main__":
	unittest.main()
