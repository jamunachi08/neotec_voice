frappe.ui.form.on("Voice Call", {
	refresh(frm) {
		if (frm.doc.party_type && frm.doc.party) {
			frm.add_custom_button(__("Open {0}", [__(frm.doc.party_type)]), () =>
				frappe.set_route("Form", frm.doc.party_type, frm.doc.party)
			);
		}
		const number = frm.doc.direction === "Inbound" ? frm.doc.from_number : frm.doc.to_number;
		if (number && window.neotec && neotec.voice && neotec.voice.is_agent()) {
			frm.add_custom_button(__("Call back"), () =>
				neotec.voice.dial(number, frm.doc.party_type, frm.doc.party)
			);
		}
		const url = frm.doc.recording_file || frm.doc.recording_url;
		if (frm.doc.recording_file) {
			frm.set_intro(`<audio controls preload="none" style="width:100%" src="${frappe.utils.escape_html(url)}"></audio>`);
		}
	},
});
