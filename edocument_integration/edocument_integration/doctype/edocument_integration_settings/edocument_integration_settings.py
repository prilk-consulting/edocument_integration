# Copyright (c) 2025, Prilk Consulting BV and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class EDocumentIntegrationSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		account_id: DF.Data | None
		api_key: DF.Data | None
		api_secret: DF.Data | None
		base_url: DF.Data | None
		company: DF.Link | None
		company_id: DF.Data | None
		edocument_integrator: DF.Literal["B2B Router", "Recommand", "Peppyrus"]
		edocument_profile: DF.Link | None
	# end: auto-generated types

	def process_incoming_document(self, xml_bytes: bytes, document_id: str | None = None):
		# Process incoming document XML and create EDocument
		try:
			# Check for duplicate reference ID
			if document_id:
				existing = frappe.db.exists("EDocument", {"reference": document_id})
				if existing:
					return {
						"skipped": True,
						"reason": "duplicate",
						"reference": document_id,
						"existing": existing,
					}

			# Ensure xml_bytes is bytes
			if not isinstance(xml_bytes, bytes):
				raise ValueError(f"xml_bytes must be bytes, got {type(xml_bytes)}")

			# Detect profile from XML
			from edocument.edocument.doctype.edocument.edocument import _detect_profile_from_xml

			profile_name = _detect_profile_from_xml(xml_bytes)
			if not profile_name:
				raise ValueError("Could not detect e-document profile from XML")

			# Create EDocument WITHOUT profile first (to skip validation)
			edocument = frappe.get_doc(
				{
					"doctype": "EDocument",
					"reference": document_id,  # Store provider's document ID as reference
					"direction": "Incoming",  # Polled documents are incoming
				}
			)
			edocument.insert(ignore_permissions=True)
			frappe.db.commit()

			# Attach XML file
			filename = f"document_{document_id}.xml"
			file_doc = frappe.get_doc(
				{
					"doctype": "File",
					"file_name": filename,
					"attached_to_doctype": "EDocument",
					"attached_to_name": edocument.name,
					"is_private": 1,
					"content": xml_bytes,
				}
			)
			file_doc.insert(ignore_permissions=True)
			frappe.db.commit()

			# Now set profile and xml_file to trigger validation
			edocument = frappe.get_doc("EDocument", edocument.name)
			edocument.xml_file = file_doc.file_url
			edocument.edocument_profile = profile_name
			edocument.save(ignore_permissions=True)
			frappe.db.commit()

			return {
				"edocument": edocument.name,
				"profile": profile_name,
				"status": edocument.status,
			}
		except Exception as e:
			frappe.db.rollback()
			frappe.log_error(
				f"Failed to process incoming document (document_id: {document_id}): {e!s}\nTraceback: {frappe.get_traceback()}",
				"Document Processing Error",
			)
			raise

	@frappe.whitelist()
	def poll_incoming_documents(self):
		# Poll provider inbox for new incoming documents and process them
		if not self.edocument_profile:
			frappe.throw(_("EDocument Profile is required"))

		if not self.edocument_integrator:
			frappe.throw(_("EDocument Integrator is required"))

		# Get integration settings from this document
		integration_settings = {
			"api_key": self.api_key,
			"api_secret": self.get_password("api_secret"),
			"base_url": self.base_url,
			"company": self.company,
			"company_id": self.company_id,
			"account_id": self.account_id,
			"edocument_integrator": self.edocument_integrator,
			"edocument_profile": self.edocument_profile,
		}

		# Route to appropriate provider handler to fetch XMLs
		if self.edocument_integrator == "Recommand":
			from edocument_integration.recommand_api import poll_inbox

			poll_result = poll_inbox(integration_settings=integration_settings, company_id=self.company_id)
		elif self.edocument_integrator == "B2B Router":
			from edocument_integration.b2brouter_api import poll_inbox

			poll_result = poll_inbox(integration_settings=integration_settings, company_id=self.company_id)
		elif self.edocument_integrator == "Peppyrus":
			from edocument_integration.peppyrus_api import poll_inbox

			poll_result = poll_inbox(integration_settings=integration_settings, company_id=self.company_id)
		else:
			frappe.throw(_("Unsupported E-document integrator: {0}").format(self.edocument_integrator))

		# Process each document XML to create EDocument records
		documents = poll_result.get("invoices", [])
		if not documents:
			return {"status": "success", "message": "No new documents found", "processed": 0}

		processed = []
		skipped = []
		for document_data in documents:
			try:
				xml_bytes = document_data.get("xml_bytes")
				document_id = document_data.get("document_id")

				if not xml_bytes:
					frappe.log_error(
						f"No xml_bytes found for document {document_id}", "Document Processing Error"
					)
					continue

				# Ensure xml_bytes is bytes (it might have been serialized)
				if isinstance(xml_bytes, str):
					xml_bytes = xml_bytes.encode("utf-8")
				elif not isinstance(xml_bytes, bytes):
					frappe.log_error(
						f"Invalid xml_bytes type: {type(xml_bytes)} for document {document_id}",
						"Document Processing Error",
					)
					continue

				# Process document using process_incoming_document method
				result = self.process_incoming_document(xml_bytes, document_id)
				if result.get("skipped"):
					skipped.append(result)
				else:
					processed.append(result)
			except Exception as e:
				frappe.log_error(
					f"Failed to process document {document_data.get('document_id')}: {e!s}\nTraceback: {frappe.get_traceback()}",
					"Document Processing Error",
				)

		message_parts = []
		if processed:
			message_parts.append(f"Processed {len(processed)} document(s)")
		if skipped:
			message_parts.append(f"Skipped {len(skipped)} duplicate(s)")

		return {
			"status": "success",
			"message": ", ".join(message_parts) if message_parts else "No new documents found",
			"processed": len(processed),
			"skipped": len(skipped),
			"documents": processed,
		}
