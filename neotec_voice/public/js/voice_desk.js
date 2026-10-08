// Neotec Voice Hub — desk-wide integration (loaded on every desk page).
frappe.provide("neotec.voice");

(() => {
	const V = neotec.voice;
	const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
	const api = (m, a) => frappe.xcall(`neotec_voice.api.v1.${m}`, a || {});
	const PHONE_FIELDS = ["mobile_no", "phone", "phone_no", "cell_number", "contact_mobile", "contact_phone", "whatsapp_no", "emergency_phone_number"];

	V.boot = () => (frappe.boot && frappe.boot.neotec_voice) || {};
	V.is_agent = () => !!V.boot().agent;
	V.cards = {};

	// ---------------------------------------------------------- dialling
	V.dial = async function (number, link_doctype, link_name) {
		if (!number) return;
		if (V.softphone && V.softphone.ready && V.softphone.ready()) {
			const prep = await api("prepare_call", { to: number, link_doctype, link_name });
			return V.softphone.call(number, prep.call);
		}
		frappe.show_alert({ message: __("Calling {0}: your phone will ring first", [esc(number)]), indicator: "blue" }, 6);
		try {
			await api("call", { to: number, link_doctype, link_name });
		} catch (e) {
			/* server already showed the reason */
		}
	};

	V.open_dialer = function () {
		const d = new frappe.ui.Dialog({
			title: __("Call"),
			fields: [
				{ fieldname: "number", fieldtype: "Data", options: "Phone", label: __("Number"), reqd: 1 },
				{ fieldname: "status", fieldtype: "Select", label: __("My status"), options: ["Available", "Away", "Offline"].join("\n"),
					default: (V.boot().agent || {}).status || "Available" },
			],
			primary_action_label: __("Call"),
			primary_action(v) {
				d.hide();
				V.dial(v.number);
			},
		});
		d.fields_dict.status.$input.on("change", async function () {
			const r = await api("set_status", { status: this.value });
			if (r) V.boot().agent.status = r.status;
			frappe.show_alert({ message: __("Status: {0}", [__(this.value)]), indicator: "green" });
		});
		d.show();
	};

	// ------------------------------------------------------ call card UI
	const host = () => {
		let $h = $(".ntv-cards");
		if (!$h.length) $h = $('<div class="ntv-cards" aria-live="polite"></div>').appendTo(document.body);
		return $h;
	};

	const who = (c) => c.party_name || c.number || __("Unknown caller");

	const card = (c) => {
		let $c = V.cards[c.call];
		if (!$c) {
			$c = $(`<div class="ntv-card" role="dialog" aria-label="${__("Call")}"></div>`).appendTo(host());
			V.cards[c.call] = $c;
		}
		return $c;
	};

	const close = (call) => {
		const $c = V.cards[call];
		if ($c) $c.remove();
		delete V.cards[call];
	};

	const ringing = (c) => {
		const $c = card(c);
		$c.attr("data-state", "ringing").html(`
			<div class="ntv-card-head"><span class="ntv-pulse"></span>${c.direction === "Inbound" ? __("Incoming call") : __("Calling")}</div>
			<div class="ntv-card-who">${esc(who(c))}</div>
			<div class="ntv-card-sub">${esc(c.number || "")}${c.queue ? " — " + esc(c.queue) : ""}</div>
			<div class="ntv-card-actions">
				${c.party ? `<button class="btn btn-xs btn-default" data-act="open">${__("Open {0}", [__(c.party_type)])}</button>`
					: frappe.boot.user.can_create.includes("Lead") ? `<button class="btn btn-xs btn-default" data-act="lead">${__("New lead")}</button>` : ""}
				<button class="btn btn-xs btn-link" data-act="dismiss">${__("Hide")}</button>
			</div>`);
		bind($c, c);
	};

	const answered = (c) => {
		const $c = card(c);
		const started = Date.now();
		$c.attr("data-state", "talking").html(`
			<div class="ntv-card-head">${__("On call")} <span class="ntv-timer">0:00</span></div>
			<div class="ntv-card-who">${esc(who(c))}</div>
			<div class="ntv-card-sub">${esc(c.number || "")}</div>
			<textarea class="form-control ntv-notes" rows="2" placeholder="${__("Notes while you talk")}"></textarea>
			<div class="ntv-card-actions">
				${c.party ? `<button class="btn btn-xs btn-default" data-act="open">${__("Open {0}", [__(c.party_type)])}</button>` : ""}
				<button class="btn btn-xs btn-default" data-act="hangup">${__("Hang up")}</button>
			</div>`);
		const t = setInterval(() => {
			if (!document.body.contains($c[0])) return clearInterval(t);
			const s = Math.floor((Date.now() - started) / 1000);
			$c.find(".ntv-timer").text(`${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`);
		}, 1000);
		bind($c, c);
	};

	const wrap_up = (c) => {
		const $c = card(c);
		const notes = $c.find(".ntv-notes").val() || "";
		$c.attr("data-state", "wrap").html(`
			<div class="ntv-card-head">${c.answered ? __("Call ended") : __("Missed call")}</div>
			<div class="ntv-card-who">${esc(who(c))}</div>
			<div class="ntv-wrap"></div>`);
		const fg = new frappe.ui.FieldGroup({
			body: $c.find(".ntv-wrap")[0],
			fields: [
				{ fieldname: "disposition", fieldtype: "Link", options: "Voice Disposition", label: __("Outcome") },
				{ fieldname: "notes", fieldtype: "Small Text", label: __("Notes"), default: notes },
				{ fieldname: "follow_up_on", fieldtype: "Date", label: __("Follow up on") },
			],
		});
		fg.make();
		fg.set_values({ notes });
		$(`<div class="ntv-card-actions"><button class="btn btn-xs btn-primary" data-act="save">${__("Save outcome")}</button>
			${!c.answered && c.number ? `<button class="btn btn-xs btn-default" data-act="callback">${__("Call back")}</button>` : ""}
			<button class="btn btn-xs btn-link" data-act="dismiss">${__("Skip")}</button></div>`).appendTo($c);
		$c.find("[data-act=save]").on("click", async () => {
			await api("save_wrap_up", Object.assign({ call: c.call }, fg.get_values(true)));
			frappe.show_alert({ message: __("Outcome saved"), indicator: "green" });
			close(c.call);
		});
		bind($c, c);
	};

	const bind = ($c, c) => {
		$c.find("[data-act=open]").off("click").on("click", () => frappe.set_route("Form", c.party_type, c.party));
		$c.find("[data-act=lead]").off("click").on("click", () => frappe.new_doc("Lead", { mobile_no: c.number }));
		$c.find("[data-act=dismiss]").off("click").on("click", () => close(c.call));
		$c.find("[data-act=callback]").off("click").on("click", () => { close(c.call); V.dial(c.number, c.party_type, c.party); });
		$c.find("[data-act=hangup]").off("click").on("click", () => {
			if (V.softphone && V.softphone.active && V.softphone.active()) return V.softphone.hangup();
			api("hangup", { call: c.call });
		});
	};

	V.on_call = function (c) {
		if (!V.boot().screen_pop) return;
		if (c.event === "ringing") ringing(c);
		else if (c.event === "answered") answered(c);
		else if (c.event === "ended") wrap_up(c);
		else if (c.event === "taken") close(c.call);
	};

	// ----------------------------------------------- click-to-call on forms
	const phone_fields = (frm) =>
		frm.meta.fields.filter((df) => df.fieldtype === "Phone" || (df.fieldtype === "Data" && df.options === "Phone") || PHONE_FIELDS.includes(df.fieldname))
			.filter((df) => frm.doc[df.fieldname]);

	const add_call_buttons = (frm) => {
		if (!V.is_agent() || !V.boot().click_to_call || frm.is_new() || frm.doctype.startsWith("Voice ")) return;
		const group = __("Call");
		phone_fields(frm).forEach((df) => {
			frm.add_custom_button(`${__(df.label)}: ${frm.doc[df.fieldname]}`, () => V.dial(frm.doc[df.fieldname], frm.doctype, frm.docname), group);
		});
	};

	// ------------------------------------------------------------ startup
	let started = false;
	V.init = function () {
		if (started || !frappe.boot || !frappe.boot.user || frappe.session.user === "Guest") return;
		started = true;
		$(document).on("form-refresh", (e, frm) => frm && add_call_buttons(frm));
		if (!V.is_agent()) return;

		frappe.realtime.on("neotec_voice_call", V.on_call);
		const beat = () => api("heartbeat").catch(() => {});
		beat();
		setInterval(beat, 60000);

		const $nav = $(".navbar .navbar-nav").first();
		if ($nav.length && !$nav.find(".ntv-nav").length) {
			$(`<li class="nav-item ntv-nav"><button class="btn-reset nav-link" title="${__("Call")}" aria-label="${__("Call")}">
				<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
				<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z"/></svg>
				</button></li>`).prependTo($nav).find("button").on("click", V.open_dialer);
		}

		if ((V.boot().agent || {}).device === "Softphone") {
			frappe.require("/assets/neotec_voice/js/softphone.js", () => V.softphone && V.softphone.start());
		}
	};

	$(document).on("startup", V.init);
	$(() => setTimeout(V.init, 2000));
})();
