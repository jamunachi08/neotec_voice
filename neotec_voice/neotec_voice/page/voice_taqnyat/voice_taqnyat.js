frappe.pages["voice-taqnyat"].on_page_load = function (wrapper) {
	frappe.require("/assets/neotec_voice/js/vendor_setup.js", () => neotec.voice.vendor_page(wrapper, "Taqnyat"));
};

frappe.pages["voice-taqnyat"].refresh = function (wrapper) {
	if (wrapper.ntv_refresh) wrapper.ntv_refresh();
};
