// Copyright (c) 2025, Prilk Consulting BV and contributors
// For license information, please see license.txt

frappe.ui.form.on("EDocument", {
	refresh: function (frm) {
		// Send EDocument button (only for validated documents with XML)
		if (
			!frm.is_new() &&
			frm.doc.edocument_profile &&
			frm.doc.status === "Validation Successful"
		) {
			// Check for XML file (either uploaded or generated)
			let has_xml = false;

			// Check for uploaded XML file first (synchronous check)
			if (frm.doc.xml_file) {
				has_xml = true;
			} else if (frm.doc.edocument_source_document) {
				// Check for generated XML file (asynchronous check)
				frm.call({
					method: "has_xml_file",
					doc: frm.doc,
					callback: function (r) {
						if (r.message) {
							// Add button after checking for XML file
							frm.add_custom_button(
								__("Send EDocument"),
								function () {
									frappe.confirm(
										__(
											"Are you sure you want to transmit this document to Peppol Network?"
										),
										() => {
											// Show loading indicator
											frappe.show_progress(
												__("Transmitting..."),
												0,
												100,
												__("Sending document to Peppol Network")
											);

											// Call the API
											frappe.call({
												method: "edocument_integration.api.transmit_edocument",
												args: {
													edocument_name: frm.doc.name,
												},
												callback: function (r) {
													frappe.hide_progress();

													if (r.message) {
														const result = r.message;
														const transmissionId =
															result.document_id ||
															result.id ||
															result.invoice_id ||
															"N/A";

														frappe.show_alert({
															message: __(
																"E-document transmitted successfully. Transmission ID: {0}",
																[transmissionId]
															),
															indicator: "green",
														});

														// Refresh the form to show updated transmission details
														frm.reload_doc();
													}
												},
												error: function (r) {
													frappe.hide_progress();
													frappe.show_alert({
														message: __(
															"Transmission failed. Please check the logs for details."
														),
														indicator: "red",
													});
												},
											});
										}
									);
								},
								__("Actions")
							);
						}
					},
				});
				return; // Exit early, button will be added in callback
			}

			// If XML file exists (uploaded), add button immediately
			if (has_xml) {
				frm.add_custom_button(
					__("Send EDocument"),
					function () {
						frappe.confirm(
							__(
								"Are you sure you want to transmit this document to Peppol Network?"
							),
							() => {
								// Show loading indicator
								frappe.show_progress(
									__("Transmitting..."),
									0,
									100,
									__("Sending document to Peppol Network")
								);

								// Call the API
								frappe.call({
									method: "edocument_integration.api.transmit_edocument",
									args: {
										edocument_name: frm.doc.name,
									},
									callback: function (r) {
										frappe.hide_progress();

										if (r.message) {
											const result = r.message;
											const transmissionId =
												result.document_id ||
												result.id ||
												result.invoice_id ||
												"N/A";

											frappe.show_alert({
												message: __(
													"E-document transmitted successfully. Transmission ID: {0}",
													[transmissionId]
												),
												indicator: "green",
											});

											// Refresh the form to show updated transmission details
											frm.reload_doc();
										}
									},
									error: function (r) {
										frappe.hide_progress();
										frappe.show_alert({
											message: __(
												"Transmission failed. Please check the logs for details."
											),
											indicator: "red",
										});
									},
								});
							}
						);
					},
					__("Actions")
				);
			}
		}
	},
});
