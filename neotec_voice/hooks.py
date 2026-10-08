app_name = "neotec_voice"
app_title = "Neotec Voice Hub"
app_publisher = "Neotec Integrated Solutions"
app_description = "Vendor-neutral voice, contact-centre and voice-reseller platform for Frappe / ERPNext"
app_email = ""
app_license = "Proprietary"
app_version = "1.0.1"

add_to_apps_screen = [
	{
		"name": "neotec_voice",
		"logo": "/assets/neotec_voice/images/neotec-voice.svg",
		"title": "Voice Hub",
		"route": "/app/voice-hub",
		"has_permission": "neotec_voice.boot.has_app_permission",
	}
]

app_include_js = ["/assets/neotec_voice/js/voice_desk.js"]
app_include_css = ["/assets/neotec_voice/css/voice.css"]

boot_session = "neotec_voice.boot.boot_session"

after_install = "neotec_voice.install.after_install"
after_migrate = "neotec_voice.install.after_migrate"

scheduler_events = {
	"cron": {"* * * * *": ["neotec_voice.core.agents.release_wrap_up"]},
	"daily": ["neotec_voice.core.retention.run"],
	"monthly": ["neotec_voice.core.billing.run_monthly"],
}

# Calls appear on the timeline of every linked document (Lead, Customer, Contact, ...)
additional_timeline_content = {"*": ["neotec_voice.core.timeline.timeline_items"]}

default_log_clearing_doctypes = {"Voice Call Event": 90, "Voice OTP": 7}

# Extension point: other apps add carriers/PBXs here (dotted path to a BaseProvider subclass)
neotec_voice_providers = []
