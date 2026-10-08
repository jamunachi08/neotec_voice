"""Show calls on the timeline of every linked document (Lead, Customer, ...)."""

import frappe
from frappe import _
from frappe.utils import escape_html

SKIP = {"Voice Call", "Voice Call Event", "Voice Agent", "Voice Settings"}
ICON = {"Inbound": "↙", "Outbound": "↗", "Internal": "↔"}


def timeline_items(doctype, docname):
	if doctype in SKIP or not frappe.has_permission("Voice Call", "read"):
		return []
	rows = frappe.db.sql(
		"""select c.name, c.direction, c.status, c.from_number, c.to_number, c.talk_time,
			c.agent, c.summary, c.disposition, c.creation
		from `tabVoice Call` c
		inner join `tabDynamic Link` l on l.parent = c.name and l.parenttype = 'Voice Call'
		where l.link_doctype = %s and l.link_name = %s
		order by c.creation desc limit 50""",
		(doctype, docname),
		as_dict=True,
	)
	items = []
	for r in rows:
		mins = f"{(r.talk_time or 0) // 60}m {(r.talk_time or 0) % 60}s"
		number = r.from_number if r.direction == "Inbound" else r.to_number
		bits = [
			f'{ICON.get(r.direction, "")} <a href="/app/voice-call/{r.name}"><b>{_(r.direction)} {_("call")}</b></a>',
			escape_html(number or ""),
			_(r.status),
			mins,
		]
		if r.agent:
			bits.append(escape_html(frappe.utils.get_fullname(r.agent)))
		if r.disposition:
			bits.append(escape_html(r.disposition))
		html = " · ".join(bits)
		if r.summary:
			html += f'<div class="text-muted small">{escape_html(r.summary[:280])}</div>'
		items.append({"icon": "call", "creation": r.creation, "content": html})
	return items
