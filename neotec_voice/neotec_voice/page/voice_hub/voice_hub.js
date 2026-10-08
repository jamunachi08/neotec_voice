frappe.pages["voice-hub"].on_page_load = function (wrapper) {
	frappe.require("/assets/neotec_voice/js/vendor_setup.js", () => neotec.voice.hub_page(wrapper));
};
