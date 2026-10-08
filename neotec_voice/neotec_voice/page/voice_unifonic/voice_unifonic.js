frappe.pages["voice-unifonic"].on_page_load = function (wrapper) {
	frappe.require("/assets/neotec_voice/js/vendor_setup.js", () => neotec.voice.vendor_page(wrapper, "Unifonic"));
};

frappe.pages["voice-unifonic"].refresh = function (wrapper) {
	if (wrapper.ntv_refresh) wrapper.ntv_refresh();
};
