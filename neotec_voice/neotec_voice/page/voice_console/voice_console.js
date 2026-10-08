frappe.pages["voice-console"].on_page_load = function (wrapper) {
	frappe.require("/assets/neotec_voice/js/vendor_setup.js", () => neotec.voice.console_page(wrapper));
};
