frappe.ui.form.on("Voice Provider Account", {
	refresh(frm) {
		if (frm.is_new()) {
			frm.set_intro(__("Create provider accounts from the Voice Hub so credentials are stored securely."), "blue");
			frm.add_custom_button(__("Open Voice Hub"), () => frappe.set_route("voice-hub"));
			return;
		}
		frm.add_custom_button(__("Open setup page"), () =>
			frm.call("get_setup_route").then((r) => {
				frappe.route_options = { account: frm.doc.name };
				frappe.set_route(r.message);
			})
		);
	},
});
