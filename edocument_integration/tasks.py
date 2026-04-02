import frappe


def poll_all_incoming_documents():
	"""Poll incoming documents for all EDocument Integration Settings."""
	settings_list = frappe.get_all(
		"EDocument Integration Settings",
		pluck="name",
	)

	for settings_name in settings_list:
		try:
			settings = frappe.get_doc("EDocument Integration Settings", settings_name)
			settings.poll_incoming_documents()
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				f"Auto-poll failed for {settings_name}",
				"EDocument Auto-Poll Error",
			)
