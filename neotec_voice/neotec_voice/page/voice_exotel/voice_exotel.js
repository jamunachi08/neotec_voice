frappe.pages["voice-exotel"].on_page_load = function (wrapper) {
	frappe.require("/assets/neotec_voice/js/vendor_setup.js", () => neotec.voice.vendor_page(wrapper, "Exotel"));
};

frappe.pages["voice-exotel"].refresh = function (wrapper) {
	if (wrapper.ntv_refresh) wrapper.ntv_refresh();
};
