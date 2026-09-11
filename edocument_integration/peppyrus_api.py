# Copyright (c) 2026, Prilk Consulting BV and contributors
# For license information, please see license.txt

"""
Peppyrus PEPPOL API Client

This module provides a client for interacting with the Peppyrus PEPPOL API.
"""

import base64
import json
from typing import Any

import frappe
import requests

BASE_URL = "https://api.peppyrus.be/v1"
TEST_BASE_URL = "https://api.test.peppyrus.be/v1"
TIMEOUT_SECONDS = 30


class PeppyrusAPIError(Exception):
	"""Custom exception for Peppyrus API errors."""

	...


def _normalize_participant_id(endpoint_value: str | None, scheme_id: str | None) -> str:
	if not endpoint_value:
		raise PeppyrusAPIError("EndpointID value is missing in XML")

	participant_scheme = scheme_id or "0088"
	return f"{participant_scheme}:{endpoint_value.strip()}"


def _is_participant_id(value: str | None) -> bool:
	return bool(value and ":" in value)


def _extract_message_fields(xml_content: str | bytes) -> dict[str, str]:
	from lxml import etree as ET

	try:
		from edocument.edocument.profiles.peppol import UBL_NAMESPACES

		xml_bytes = xml_content.encode("utf-8") if isinstance(xml_content, str) else xml_content
		root = ET.fromstring(xml_bytes)

		supplier_endpoint = root.find(
			".//cac:AccountingSupplierParty/cac:Party/cbc:EndpointID", UBL_NAMESPACES
		)
		customer_endpoint = root.find(
			".//cac:AccountingCustomerParty/cac:Party/cbc:EndpointID", UBL_NAMESPACES
		)

		if supplier_endpoint is None:
			raise PeppyrusAPIError(
				"Sender EndpointID not found in XML. Please configure electronic address for supplier."
			)

		if customer_endpoint is None:
			raise PeppyrusAPIError(
				"Recipient EndpointID not found in XML. Please configure electronic address for customer."
			)

		customization_id = root.findtext(".//cbc:CustomizationID", namespaces=UBL_NAMESPACES)
		profile_id = root.findtext(".//cbc:ProfileID", namespaces=UBL_NAMESPACES)
		ubl_version = root.findtext(".//cbc:UBLVersionID", namespaces=UBL_NAMESPACES) or "2.1"

		if not customization_id:
			raise PeppyrusAPIError("CustomizationID not found in XML; cannot determine document type")

		if not profile_id:
			raise PeppyrusAPIError("ProfileID not found in XML; cannot determine process type")

		if root.tag.startswith("{"):
			root_namespace, root_name = root.tag[1:].split("}", 1)
		else:
			root_namespace = ""
			root_name = root.tag

		return {
			"sender": _normalize_participant_id(supplier_endpoint.text, supplier_endpoint.get("schemeID")),
			"recipient": _normalize_participant_id(customer_endpoint.text, customer_endpoint.get("schemeID")),
			"process_type": f"cenbii-procid-ubl::{profile_id.strip()}",
			"document_type": (
				f"busdox-docid-qns::{root_namespace}::{root_name}##{customization_id.strip()}::{ubl_version.strip()}"
			),
		}
	except ET.ParseError as exc:
		raise PeppyrusAPIError(f"Failed to parse XML to extract message metadata: {exc!s}") from exc
	except PeppyrusAPIError:
		raise
	except Exception as exc:
		raise PeppyrusAPIError(f"Failed to extract message metadata from XML: {exc!s}") from exc


class PeppyrusAPIClient:
	"""Client for interacting with the Peppyrus PEPPOL API using API key header auth."""

	def __init__(self, api_key: str, base_url: str = BASE_URL):
		self.api_key = api_key
		self.base_url = base_url.rstrip("/")
		self.session = requests.Session()

		# Use API key header like B2B Router
		self.session.headers.update(
			{
				"X-Api-Key": api_key,
				"Accept": "application/json",
				"Content-Type": "application/json",
				"User-Agent": "Frappe-EDocument/1.0",
			}
		)

	def _make_request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
		url = f"{self.base_url}{endpoint}"

		try:
			response = self.session.request(method, url, timeout=TIMEOUT_SECONDS, **kwargs)
			return response
		except requests.exceptions.RequestException as exc:
			error_msg = f"Peppyrus API request failed: {exc!s}"
			frappe.log_error(error_msg, "Peppyrus API Error")
			raise PeppyrusAPIError(error_msg) from exc

	def _parse_json_response(self, response: requests.Response) -> dict[str, Any] | list[Any] | bool:
		if not response.ok:
			try:
				error_data = response.json()
				error_text = json.dumps(error_data)
			except json.JSONDecodeError:
				error_text = response.text

			error_msg = f"Peppyrus API error ({response.status_code}): {error_text}"
			frappe.log_error(error_msg, "Peppyrus API Error")
			raise PeppyrusAPIError(error_msg)

		if not response.content:
			return {}

		try:
			return response.json()
		except json.JSONDecodeError as exc:
			error_msg = f"Peppyrus API returned invalid JSON: {response.text}"
			frappe.log_error(error_msg, "Peppyrus API Error")
			raise PeppyrusAPIError(error_msg) from exc

	# DOCUMENT TRANSMISSION METHODS

	def send_document(self, xml_content: str | bytes) -> dict[str, Any]:
		"""Send a PEPPOL document via Peppyrus.

		The payload follows the documented MessageBody schema.
		"""
		endpoint = "/message"

		if isinstance(xml_content, bytes):
			xml_bytes = xml_content
		else:
			xml_bytes = xml_content.encode("utf-8")

		message_fields = _extract_message_fields(xml_bytes)

		payload = {
			"sender": message_fields["sender"],
			"recipient": message_fields["recipient"],
			"processType": message_fields["process_type"],
			"documentType": message_fields["document_type"],
			"fileContent": base64.b64encode(xml_bytes).decode("utf-8"),
		}

		try:
			response = self._make_request("POST", endpoint, json=payload)
			result = self._parse_json_response(response)
			if not isinstance(result, dict):
				raise PeppyrusAPIError("Unexpected Peppyrus response when posting message")
			document_id = result.get("id", "unknown")

			return {
				"status": "success",
				"document_id": document_id,
				"sender": message_fields["sender"],
				"recipient": message_fields["recipient"],
				"response": result,
			}

		except PeppyrusAPIError:
			raise
		except Exception as e:
			error_msg = f"Peppyrus document transmission failed: {e!s}"
			frappe.log_error(error_msg, "Peppyrus Transmission Error")
			raise PeppyrusAPIError(error_msg)

	def list_messages(
		self,
		folder: str | None = None,
		sender: str | None = None,
		receiver: str | None = None,
		confirmed: bool | None = None,
		page: int = 1,
		per_page: int = 50,
	) -> dict[str, Any]:
		endpoint = "/message/list"
		params = {"page": page, "perPage": min(per_page, 100)}
		if folder:
			params["folder"] = folder
		if sender:
			params["sender"] = sender
		if receiver:
			params["receiver"] = receiver
		if confirmed is not None:
			params["confirmed"] = str(confirmed).lower()
		try:
			response = self._make_request("GET", endpoint, params=params)
			result = self._parse_json_response(response)
			if not isinstance(result, dict):
				raise PeppyrusAPIError("Unexpected Peppyrus response when listing messages")
			return result
		except requests.exceptions.RequestException as e:
			error_msg = f"Peppyrus list messages failed: {e!s}"
			frappe.log_error(error_msg, "Peppyrus Message List Error")
			raise PeppyrusAPIError(error_msg)

	def get_message(self, message_id: str) -> dict[str, Any]:
		endpoint = f"/message/{message_id}"
		try:
			response = self._make_request("GET", endpoint)
			result = self._parse_json_response(response)
			if not isinstance(result, dict):
				raise PeppyrusAPIError("Unexpected Peppyrus response when fetching message")
			return result
		except requests.exceptions.RequestException as e:
			error_msg = f"Peppyrus get message failed: {e!s}"
			frappe.log_error(error_msg, "Peppyrus Get Message Error")
			raise PeppyrusAPIError(error_msg)

	def confirm_message(self, message_id: str) -> bool:
		endpoint = f"/message/{message_id}/confirm"
		response = self._make_request("PATCH", endpoint)
		result = self._parse_json_response(response)
		if not isinstance(result, bool):
			raise PeppyrusAPIError("Unexpected Peppyrus response when confirming message")
		return result

	def render_message(self, message_id: str, output_format: str = "PDF") -> dict[str, Any]:
		endpoint = f"/message/{message_id}/render"
		response = self._make_request("GET", endpoint, params={"format": output_format})
		result = self._parse_json_response(response)
		if not isinstance(result, dict):
			raise PeppyrusAPIError("Unexpected Peppyrus response when rendering message")
		return result

	def get_message_report(self, message_id: str) -> dict[str, Any]:
		endpoint = f"/message/{message_id}/report"
		response = self._make_request("GET", endpoint)
		result = self._parse_json_response(response)
		if not isinstance(result, dict):
			raise PeppyrusAPIError("Unexpected Peppyrus response when fetching message report")
		return result

	def get_organization_info(self) -> dict[str, Any]:
		response = self._make_request("GET", "/organization/info")
		result = self._parse_json_response(response)
		if not isinstance(result, dict):
			raise PeppyrusAPIError("Unexpected Peppyrus response when fetching organization info")
		return result

	def get_organization_peppol_info(self) -> dict[str, Any]:
		response = self._make_request("GET", "/organization/peppol")
		result = self._parse_json_response(response)
		if not isinstance(result, dict):
			raise PeppyrusAPIError("Unexpected Peppyrus response when fetching organization peppol info")
		return result

	def lookup_participant(self, participant_id: str) -> dict[str, Any]:
		response = self._make_request("GET", "/peppol/lookup", params={"participantId": participant_id})
		result = self._parse_json_response(response)
		if not isinstance(result, dict):
			raise PeppyrusAPIError("Unexpected Peppyrus response when looking up participant")
		return result

	def best_match_participant(self, vat_number: str, country_code: str) -> dict[str, Any]:
		response = self._make_request(
			"GET", "/peppol/bestMatch", params={"vatNumber": vat_number, "countryCode": country_code}
		)
		result = self._parse_json_response(response)
		if not isinstance(result, dict):
			raise PeppyrusAPIError("Unexpected Peppyrus response when finding participant best match")
		return result

	def search_peppol_directory(self, **search_params) -> list[dict[str, Any]]:
		response = self._make_request("GET", "/peppol/search", params=search_params)
		result = self._parse_json_response(response)
		if not isinstance(result, list):
			raise PeppyrusAPIError("Unexpected Peppyrus response when searching directory")
		return result

	def get_documents(self, team_id: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
		page = (offset // limit) + 1 if limit else 1
		return self.list_messages(page=page, per_page=limit)

	def get_inbox(self, team_id: str | None = None, company_id: str | None = None) -> dict[str, Any]:
		params: dict[str, Any] = {"folder": "INBOX"}
		if _is_participant_id(company_id):
			params["receiver"] = company_id
		return self.list_messages(**params)

	def get_document_status(self, team_id: str | None, document_id: str) -> dict[str, Any]:
		try:
			return self.get_message(document_id)
		except requests.exceptions.RequestException as e:
			error_msg = f"Peppyrus get document status failed: {e!s}"
			frappe.log_error(error_msg, "Peppyrus Document Status Error")
			raise PeppyrusAPIError(error_msg)


# HELPER FUNCTIONS


def get_peppyrus_client(integration_settings: dict[str, Any]) -> PeppyrusAPIClient:
	if not integration_settings or not isinstance(integration_settings, dict):
		raise PeppyrusAPIError("Peppyrus integration settings must be provided as a dict")

	api_key = integration_settings.get("api_key")
	base_url = integration_settings.get("base_url", BASE_URL)

	if not api_key:
		raise PeppyrusAPIError("Peppyrus API key not configured")

	return PeppyrusAPIClient(api_key, base_url)


def transmit_invoice(xml_content: str, invoice_doc=None, integration_settings=None) -> dict[str, Any]:
	if not integration_settings or not isinstance(integration_settings, dict):
		raise PeppyrusAPIError(
			"Peppyrus integration settings are required for transmission and must be a dict"
		)

	try:
		client = get_peppyrus_client(integration_settings)
		transmission_result = client.send_document(xml_content=xml_content)

		return {
			"status": "success",
			"document_id": transmission_result.get("document_id"),
			"recipient": transmission_result.get("recipient"),
			"sender": transmission_result.get("sender"),
			"response": transmission_result,
		}

	except Exception as e:
		error_msg = f"Peppyrus transmission failed: {e!s}"
		frappe.log_error(error_msg, "Peppyrus Transmission Error")
		raise PeppyrusAPIError(error_msg)


def validate_peppyrus_connection(api_key: str, base_url: str = BASE_URL) -> dict[str, Any]:
	try:
		client = PeppyrusAPIClient(api_key, base_url)
		organization_info = client.get_organization_info()
		organization_name = organization_info.get("name", "Peppyrus organization")
		return {"status": "success", "message": f"Peppyrus API connection successful for {organization_name}"}
	except Exception as e:
		return {"status": "error", "message": f"Peppyrus API connection failed: {e!s}"}


def verify_recipient(participant_id: str, integration_settings: dict[str, Any]) -> dict[str, Any]:
	client = get_peppyrus_client(integration_settings)
	lookup_result = client.lookup_participant(participant_id)
	services = lookup_result.get("services") or []
	return {
		"status": "success",
		"participant_id": lookup_result.get("participantId", participant_id),
		"scheme": lookup_result.get("scheme"),
		"services": services,
		"can_receive": bool(services),
	}


def poll_inbox(
	integration_settings: dict[str, Any] | None = None, company_id: str | None = None
) -> dict[str, Any]:
	if not integration_settings or not isinstance(integration_settings, dict):
		raise PeppyrusAPIError(
			"Peppyrus integration settings are required for inbox polling and must be a dict"
		)

	client = get_peppyrus_client(integration_settings)
	inbox_result = client.get_inbox(company_id=company_id or integration_settings.get("company_id"))
	documents = inbox_result.get("items", []) or []

	if not documents:
		return {"status": "success", "message": "No new invoices found", "invoices": []}

	invoices = []
	for doc in documents:
		try:
			document_id = doc.get("id")
			if not document_id:
				frappe.log_error("Message entry is missing an ID", "Peppyrus Inbox Polling Error")
				continue

			doc_details = client.get_message(document_id)

			file_content = doc_details.get("fileContent")
			if not file_content:
				frappe.log_error(
					f"No 'fileContent' key found in response for {document_id}. Response keys: {list(doc_details.keys())}",
					"Peppyrus Inbox Polling Error",
				)
				continue

			xml_bytes = base64.b64decode(file_content)
			invoices.append({"xml_bytes": xml_bytes, "document_id": document_id, "metadata": doc_details})
		except Exception as e:
			frappe.log_error(
				f"Failed to fetch document {doc.get('id')}: {e!s}\nTraceback: {frappe.get_traceback()}",
				"Peppyrus Inbox Polling Error",
			)

	return {"status": "success", "message": f"Found {len(invoices)} invoice(s)", "invoices": invoices}
