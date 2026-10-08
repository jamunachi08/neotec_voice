// Neotec Voice Hub — Hub, per-vendor setup and Console pages.
frappe.provide("neotec.voice");

(() => {
	const V = neotec.voice;
	const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
	const setup = (method, args) => frappe.xcall(`neotec_voice.api.setup.${method}`, args || {});
	const v1 = (method, args) => frappe.xcall(`neotec_voice.api.v1.${method}`, args || {});

	const CAPS = {
		outbound: __("Click-to-call"),
		inbound: __("Inbound routing by Neotec"),
		events: __("Live call events"),
		crm: __("Caller lookup & journaling"),
		softphone: __("Browser softphone"),
		recording: __("Recordings"),
		sms: __("SMS"),
		numbers: __("Number sync"),
		provision: __("One-click provisioning"),
		hangup: __("Remote hang-up"),
	};
	const CATEGORIES = [
		["IP-PBX", __("Your own PBX"), __("Keep calls on infrastructure you control and plug in any SIP trunk. Neotec decides who rings.")],
		["Cloud CPaaS", __("Cloud carriers"), __("Numbers and minutes from a cloud operator, with routing still decided in Neotec.")],
		["SMS Gateway", __("SMS gateways"), __("Notifications and one-time passwords with registered sender IDs.")],
		["Open Connector", __("Anything else"), __("Describe any HTTP-capable PBX or carrier with templates — no code.")],
	];

	const health = (v) => {
		if (!v.accounts) return { cls: "off", text: __("Not connected") };
		if (!v.enabled) return { cls: "off", text: __("{0} account(s), disabled", [v.accounts]) };
		const last = v.last_webhook_on ? __("last event {0}", [frappe.datetime.comment_when(v.last_webhook_on, true)]) : __("no events yet");
		return v.healthy ? { cls: "ok", text: `${__("Connected")}, ${last}` } : { cls: "warn", text: __("Connected, not tested") };
	};

	const fmt_secs = (s) => {
		s = Math.round(s || 0);
		return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
	};

	// ------------------------------------------------------------------ Hub
	V.hub_page = function (wrapper) {
		const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Voice Hub"), single_column: true });
		page.set_primary_action(__("Open console"), () => frappe.set_route("voice-console"));
		[
			[__("Agents"), "Voice Agent"], [__("Queues"), "Voice Queue"], [__("Inbound routes"), "Voice Route"],
			[__("Outbound routes"), "Voice Outbound Route"], [__("Numbers"), "Voice Number"],
			[__("Rate plans"), "Voice Rate Plan"], [__("Call log"), "Voice Call"],
		].forEach(([label, dt]) => page.add_menu_item(label, () => frappe.set_route("List", dt)));
		page.add_menu_item(__("Settings"), () => frappe.set_route("Form", "Voice Settings"));

		const $root = $('<div class="ntv ntv-hub"></div>').appendTo(page.main);
		const render = async () => {
			const [hub, k] = await Promise.all([setup("hub"), v1("kpis").catch(() => ({}))]);
			const connected = hub.vendors.filter((v) => v.enabled).length;
			const sla = k.sla_pct == null ? "—" : `${k.sla_pct}%`;
			let html = `
				<section class="ntv-summary">
					<p class="ntv-lede">${__("{0} of {1} connectors active, {2} agents, {3} numbers.", [connected, hub.vendors.length, hub.agents, hub.numbers])}</p>
					<dl class="ntv-figures">
						<div><dt>${__("Calls today")}</dt><dd>${k.total || 0}</dd></div>
						<div><dt>${__("Answered")}</dt><dd>${k.answered || 0}</dd></div>
						<div><dt>${__("Missed")}</dt><dd>${k.missed || 0}</dd></div>
						<div><dt>${__("Answered in 20s")}</dt><dd>${sla}</dd></div>
						<div><dt>${__("Avg talk")}</dt><dd>${fmt_secs(k.aht)}</dd></div>
					</dl>
				</section>`;
			CATEGORIES.forEach(([cat, title, note]) => {
				const items = hub.vendors.filter((v) => v.category === cat);
				if (!items.length) return;
				html += `<section class="ntv-rack"><header><h3>${title}</h3><p>${note}</p></header><div class="ntv-ports">`;
				items.forEach((v) => {
					const h = health(v);
					html += `
						<button class="ntv-port" style="--vc:${esc(v.color)}" data-page="${esc(v.page)}">
							<span class="ntv-port-name">${esc(v.label)}</span>
							<span class="ntv-port-tag">${esc(__(v.tagline))}</span>
							<span class="ntv-port-caps">${v.capabilities.filter((c) => CAPS[c]).slice(0, 4).map((c) => CAPS[c]).join(", ")}</span>
							<span class="ntv-signal ntv-${h.cls}">${esc(h.text)}</span>
						</button>`;
				});
				html += "</div></section>";
			});
			$root.html(html);
			$root.find(".ntv-port").on("click", function () {
				frappe.set_route($(this).data("page"));
			});
		};
		render();
		frappe.realtime.on("neotec_voice_board", frappe.utils.debounce(() => {
			if (frappe.get_route_str() === "voice-hub") render();
		}, 3000));
	};

	// --------------------------------------------------------- Vendor page
	V.vendor_page = function (wrapper, vendor) {
		const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Loading…"), single_column: true });
		const $root = $('<div class="ntv ntv-setup"></div>').appendTo(page.main);
		const state = { meta: null, accounts: [], acc: null, data: null, step: "credentials", fg: null };

		const steps = () => {
			const caps = state.meta.capabilities;
			const list = [["credentials", __("Credentials")], ["connect", __("Connect the vendor")]];
			if (caps.some((c) => ["outbound", "inbound", "events", "crm", "softphone"].includes(c))) list.push(["agents", __("Map agents")]);
			if (caps.includes("numbers") || caps.includes("inbound")) list.push(["numbers", __("Numbers")]);
			list.push(["activity", __("Activity")]);
			return list;
		};

		const load = async (account) => {
			const r = await setup("vendor", { vendor });
			state.meta = r.meta;
			state.accounts = r.accounts;
			page.set_title(__("{0} setup", [r.meta.label]));
			const wanted = account || (frappe.route_options && frappe.route_options.account);
			frappe.route_options = null;
			const pick = state.accounts.find((a) => a.name === wanted) || state.accounts[0];
			state.acc = pick ? pick.name : null;
			state.data = state.acc ? await setup("account", { name: state.acc }) : null;
			if (!state.acc) state.step = "credentials";
			shell();
			actions();
		};
		wrapper.ntv_refresh = () => {
			if (frappe.route_options && frappe.route_options.account) load(frappe.route_options.account);
		};

		const actions = () => {
			page.clear_primary_action();
			page.clear_secondary_action();
			page.clear_menu();
			page.set_primary_action(state.acc ? __("Save") : __("Create account"), save);
			if (!state.acc) return;
			page.set_secondary_action(__("Test connection"), test);
			const caps = state.meta.capabilities;
			if (caps.includes("provision")) page.add_menu_item(__("Point vendor numbers to Neotec"), provision);
			if (caps.includes("numbers")) page.add_menu_item(__("Sync numbers"), sync_numbers);
			if (caps.includes("sms")) page.add_menu_item(__("Send test SMS"), test_sms);
			page.add_menu_item(__("Add another {0} account", [state.meta.label]), () => { state.acc = null; state.data = null; state.step = "credentials"; shell(); actions(); });
			page.add_menu_item(__("Rotate webhook token"), rotate);
			page.add_menu_item(__("Open account record"), () => frappe.set_route("Form", "Voice Provider Account", state.acc));
			page.add_menu_item(__("Voice Hub"), () => frappe.set_route("voice-hub"));
		};

		const shell = () => {
			const m = state.meta;
			const d = state.data;
			const options = state.accounts.map((a) => `<option value="${esc(a.name)}" ${a.name === state.acc ? "selected" : ""}>${esc(a.name)}${a.enabled ? "" : " (" + __("disabled") + ")"}</option>`).join("");
			const signal = !d ? `<span class="ntv-signal ntv-off">${__("New account")}</span>`
				: d.last_test_ok ? `<span class="ntv-signal ntv-ok">${__("Tested {0}", [frappe.datetime.comment_when(d.last_test_on, true)])}</span>`
				: `<span class="ntv-signal ntv-warn">${d.last_test_on ? esc(d.last_test_message || __("Last test failed")) : __("Not tested yet")}</span>`;
			$root.html(`
				<header class="ntv-vendor" style="--vc:${esc(m.color)}">
					<div>
						<h2>${esc(m.label)}</h2>
						<p>${esc(__(m.description))}</p>
						<ul class="ntv-caps">${m.capabilities.filter((c) => CAPS[c]).map((c) => `<li>${CAPS[c]}</li>`).join("")}</ul>
					</div>
					<div class="ntv-account">
						${state.accounts.length ? `<label>${__("Account")}<select class="form-control input-sm ntv-acc">${options}</select></label>` : ""}
						${signal}
					</div>
				</header>
				<div class="ntv-body">
					<ol class="ntv-steps">${steps().map(([k, label]) => `<li><button data-step="${k}" class="${k === state.step ? "active" : ""}" ${!state.acc && k !== "credentials" ? "disabled" : ""}>${label}</button></li>`).join("")}</ol>
					<div class="ntv-panel"></div>
				</div>`);
			$root.find(".ntv-acc").on("change", function () { load(this.value); });
			$root.find(".ntv-steps button").on("click", function () {
				state.step = $(this).data("step");
				$root.find(".ntv-steps button").removeClass("active");
				$(this).addClass("active");
				panel();
			});
			panel();
		};

		const panel = () => {
			const $p = $root.find(".ntv-panel").empty();
			({ credentials, connect, agents, numbers, activity }[state.step] || credentials)($p);
		};

		// ---- Credentials
		const credentials = ($p) => {
			const d = state.data;
			const fields = [];
			if (!state.acc) fields.push({ fieldname: "account_name", label: __("Account name"), fieldtype: "Data", reqd: 1, description: __("e.g. Riyadh HQ, Jeddah branch") });
			fields.push(
				{ fieldname: "enabled", label: __("Enabled"), fieldtype: "Check", default: 1 },
				{ fieldname: "is_default", label: __("Default carrier"), fieldtype: "Check" },
				{ fieldname: "buy_rate_plan", label: __("Buy rate plan"), fieldtype: "Link", options: "Voice Rate Plan" },
				{ fieldtype: "Section Break", label: __("{0} credentials", [state.meta.label]) }
			);
			state.meta.fields.forEach((f) => {
				fields.push({
					fieldname: f.fieldname, label: __(f.label), fieldtype: f.fieldtype, reqd: f.reqd, options: f.options,
					default: f.default, placeholder: f.placeholder,
					description: f.secret && d && d.values[f.fieldname] ? __("Stored encrypted. Leave as is to keep, clear to remove.") : f.description ? __(f.description) : "",
				});
			});
			state.fg = new frappe.ui.FieldGroup({ fields, body: $p[0] });
			state.fg.make();
			if (d) state.fg.set_values(Object.assign({ enabled: d.enabled, is_default: d.is_default, buy_rate_plan: d.buy_rate_plan }, d.values));
		};

		const save = async () => {
			if (!state.fg) { state.step = "credentials"; shell(); return; }
			const vals = state.fg.get_values();
			if (!vals) return;
			const values = {};
			state.meta.fields.forEach((f) => { if (f.fieldname in vals) values[f.fieldname] = vals[f.fieldname]; });
			const name = await setup("save", {
				vendor, name: state.acc, account_name: vals.account_name, values, enabled: vals.enabled ? 1 : 0,
				is_default: vals.is_default ? 1 : 0, buy_rate_plan: vals.buy_rate_plan || null,
			});
			frappe.show_alert({ message: __("Saved"), indicator: "green" });
			const wasNew = !state.acc;
			await load(name);
			if (wasNew) { state.step = "connect"; shell(); }
		};

		const test = async () => {
			const r = await setup("test", { name: state.acc });
			frappe.msgprint({ title: r.ok ? __("Connection works") : __("Connection failed"), indicator: r.ok ? "green" : "red", message: esc(r.message || "") });
			load(state.acc);
		};

		// ---- Connect
		const connect = ($p) => {
			const d = state.data;
			let html = `<section><h4>${__("Addresses for the vendor")}</h4>
				<p class="text-muted">${__("Each address carries this account's secret token. Anyone holding it can post events, so share it only with the vendor.")}</p>`;
			d.webhooks.forEach((w, i) => {
				html += `<div class="ntv-hook"><label>${esc(w.label)}</label>
					<div class="ntv-copyrow"><input class="form-control input-sm" readonly value="${esc(w.url)}"><button class="btn btn-sm btn-default" data-copy="${i}">${__("Copy")}</button></div>
					${w.note ? `<small class="text-muted">${esc(w.note)}</small>` : ""}</div>`;
			});
			html += "</section>";
			if (d.steps.length) html += `<section><h4>${__("Steps in the {0} console", [state.meta.label])}</h4><ol class="ntv-howto">${d.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol></section>`;
			d.snippets.forEach((s, i) => {
				html += `<section class="ntv-snippet"><div class="ntv-snippet-head"><h4>${esc(s.title)}</h4>
					<span><button class="btn btn-xs btn-default" data-scopy="${i}">${__("Copy")}</button>
					<button class="btn btn-xs btn-default" data-sdl="${i}">${__("Download")}</button></span></div>
					<pre><code>${esc(s.content)}</code></pre></section>`;
			});
			$p.html(html);
			$p.find("[data-copy]").on("click", function () { frappe.utils.copy_to_clipboard(d.webhooks[$(this).data("copy")].url); });
			$p.find("[data-scopy]").on("click", function () { frappe.utils.copy_to_clipboard(d.snippets[$(this).data("scopy")].content); });
			$p.find("[data-sdl]").on("click", function () {
				const s = d.snippets[$(this).data("sdl")];
				const a = document.createElement("a");
				a.href = URL.createObjectURL(new Blob([s.content], { type: "text/plain" }));
				a.download = s.filename;
				a.click();
				setTimeout(() => URL.revokeObjectURL(a.href), 2000);
			});
		};

		// ---- Agents
		const agents = ($p) => {
			const d = state.data;
			const ids = Object.fromEntries(d.identities.map((r) => [r.agent, r.identity]));
			if (!d.agents.length) {
				$p.html(`<div class="ntv-empty"><p>${__("No agents yet. Create a Voice Agent for each user who takes calls.")}</p>
					<button class="btn btn-sm btn-primary">${__("Create agent")}</button></div>`);
				$p.find("button").on("click", () => frappe.new_doc("Voice Agent"));
				return;
			}
			$p.html(`<p class="text-muted">${__("The extension, SIP user or client id each agent has on {0}. Blank uses their default extension.", [esc(state.acc)])}</p>
				<table class="table table-sm ntv-table"><thead><tr><th>${__("Agent")}</th><th>${__("Default extension")}</th><th>${__("Identity on this account")}</th></tr></thead>
				<tbody>${d.agents.map((a) => `<tr><td>${esc(a.full_name || a.name)}</td><td>${esc(a.extension || "")}</td>
				<td><input class="form-control input-sm" data-agent="${esc(a.name)}" value="${esc(ids[a.name] || "")}"></td></tr>`).join("")}</tbody></table>
				<button class="btn btn-sm btn-primary ntv-save-ids">${__("Save mapping")}</button>`);
			$p.find(".ntv-save-ids").on("click", async () => {
				const rows = $p.find("input[data-agent]").map(function () { return { agent: $(this).data("agent"), identity: this.value }; }).get();
				await setup("save_identities", { name: state.acc, rows });
				frappe.show_alert({ message: __("Mapping saved"), indicator: "green" });
				state.data = await setup("account", { name: state.acc });
			});
		};

		// ---- Numbers
		const numbers = ($p) => {
			const d = state.data;
			const caps = state.meta.capabilities;
			$p.html(`<div class="ntv-toolbar">
					${caps.includes("numbers") ? `<button class="btn btn-sm btn-default ntv-sync">${__("Sync from {0}", [esc(state.meta.label)])}</button>` : ""}
					<button class="btn btn-sm btn-default ntv-add">${__("Add number")}</button></div>
				${d.numbers.length ? `<table class="table table-sm ntv-table"><thead><tr><th>${__("Number")}</th><th>${__("Label")}</th><th>${__("Rings")}</th></tr></thead><tbody>
				${d.numbers.map((n) => `<tr><td><a href="/app/voice-number/${encodeURIComponent(n.name)}">${esc(n.name)}</a></td><td>${esc(n.label || "")}</td>
				<td>${esc(n.default_target_type ? `${__(n.default_target_type)} ${n.default_queue || n.default_agent || ""}` : __("Inbound routes / all available agents"))}</td></tr>`).join("")}</tbody></table>`
				: `<div class="ntv-empty"><p>${__("No numbers on this account yet.")}</p></div>`}`);
			$p.find(".ntv-sync").on("click", sync_numbers);
			$p.find(".ntv-add").on("click", () => frappe.new_doc("Voice Number", { provider_account: state.acc }));
		};

		// ---- Activity
		const activity = ($p) => {
			const d = state.data;
			const s = d.stats || {};
			$p.html(`<dl class="ntv-figures">
					<div><dt>${__("Calls, 7 days")}</dt><dd>${s.calls || 0}</dd></div>
					<div><dt>${__("Answered")}</dt><dd>${s.answered || 0}</dd></div>
					<div><dt>${__("Failed")}</dt><dd>${s.failed || 0}</dd></div>
					<div><dt>${__("Last event")}</dt><dd class="ntv-small">${d.last_webhook_on ? frappe.datetime.comment_when(d.last_webhook_on) : __("none yet")}</dd></div>
				</dl>
				${d.events.length ? `<table class="table table-sm ntv-table"><thead><tr><th>${__("When")}</th><th>${__("Event")}</th><th>${__("Status")}</th><th>${__("Call")}</th></tr></thead><tbody>
				${d.events.map((e) => `<tr><td>${frappe.datetime.comment_when(e.creation)}</td><td>${esc(e.kind)}</td><td>${esc(e.status || "")}</td>
				<td>${e.call ? `<a href="/app/voice-call/${esc(e.call)}">${esc(e.call)}</a>` : ""}</td></tr>`).join("")}</tbody></table>`
				: `<div class="ntv-empty"><p>${__("Nothing received yet. Place a test call once the vendor side is connected.")}</p></div>`}`);
		};

		const provision = async () => {
			const r = await setup("provision", { name: state.acc });
			frappe.msgprint({ indicator: "green", message: esc(r.message) });
		};
		const sync_numbers = async () => {
			const r = await setup("sync_numbers", { name: state.acc });
			frappe.show_alert({ message: __("{0} new numbers", [r.created]), indicator: "green" });
			load(state.acc).then(() => { state.step = "numbers"; shell(); });
		};
		const rotate = () => frappe.confirm(__("Rotate the token? Every address and generated snippet must be updated on the vendor side."), async () => {
			await setup("rotate_token", { name: state.acc });
			frappe.show_alert({ message: __("Token rotated — update the vendor now"), indicator: "orange" });
			load(state.acc).then(() => { state.step = "connect"; shell(); });
		});
		const test_sms = () => frappe.prompt(
			[{ fieldname: "to", label: __("Send to"), fieldtype: "Data", options: "Phone", reqd: 1 },
				{ fieldname: "message", label: __("Message"), fieldtype: "Small Text" }],
			async (v) => {
				await setup("test_sms", { name: state.acc, to: v.to, message: v.message });
				frappe.show_alert({ message: __("SMS sent"), indicator: "green" });
			},
			__("Send test SMS"), __("Send"));

		load();
	};

	// -------------------------------------------------------------- Console
	V.console_page = function (wrapper) {
		const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Voice Console"), single_column: true });
		const $root = $('<div class="ntv ntv-console"></div>').appendTo(page.main);
		const STATUS_CLS = { Available: "ok", "On Call": "busy", "Wrap Up": "warn", Away: "warn", Offline: "off" };
		let me = null;

		const render = async () => {
			const [live, k, mine] = await Promise.all([v1("live"), v1("kpis"), v1("me")]);
			me = mine;
			const sla = k.sla_pct == null ? "—" : `${k.sla_pct}%`;
			$root.html(`
				${me ? `<section class="ntv-me"><span>${__("Your status")}</span>
					<div class="btn-group">${["Available", "Away", "Offline"].map((s) => `<button class="btn btn-sm ${me.status === s ? "btn-primary" : "btn-default"}" data-status="${s}">${__(s)}</button>`).join("")}</div>
					<div class="ntv-dial"><input class="form-control input-sm" placeholder="${__("Number to call")}"><button class="btn btn-sm btn-default">${__("Call")}</button></div>
				</section>` : ""}
				<dl class="ntv-figures">
					<div><dt>${__("Calls today")}</dt><dd>${k.total || 0}</dd></div>
					<div><dt>${__("Inbound")}</dt><dd>${k.inbound || 0}</dd></div>
					<div><dt>${__("Missed")}</dt><dd>${k.missed || 0}</dd></div>
					<div><dt>${__("Answered in 20s")}</dt><dd>${sla}</dd></div>
					<div><dt>${__("Avg wait")}</dt><dd>${fmt_secs(k.asa)}</dd></div>
					<div><dt>${__("Avg talk")}</dt><dd>${fmt_secs(k.aht)}</dd></div>
				</dl>
				<div class="ntv-board">
					<section><h4>${__("Agents")}</h4>
						${live.agents.length ? `<ul class="ntv-agents">${live.agents.map((a) => `<li><span class="ntv-signal ntv-${STATUS_CLS[a.status] || "off"}">${esc(__(a.status))}</span>
						<b>${esc(a.full_name || a.name)}</b><small>${a.extension ? esc(a.extension) + ", " : ""}${a.status_since ? frappe.datetime.comment_when(a.status_since, true) : ""}</small></li>`).join("")}</ul>`
						: `<div class="ntv-empty"><p>${__("No agents configured.")}</p></div>`}
					</section>
					<section><h4>${__("Live calls")}</h4>
						${live.calls.length ? `<table class="table table-sm ntv-table"><tbody>${live.calls.map((c) => `<tr>
						<td>${c.direction === "Inbound" ? "↙" : "↗"}</td>
						<td><a href="/app/voice-call/${esc(c.name)}">${esc(c.party_name || (c.direction === "Inbound" ? c.from_number : c.to_number) || c.name)}</a></td>
						<td>${esc(__(c.status))}</td><td>${esc(c.agent ? frappe.user.full_name(c.agent) : c.queue || "")}</td>
						<td>${frappe.datetime.comment_when(c.answered_at || c.creation, true)}</td></tr>`).join("")}</tbody></table>`
						: `<div class="ntv-empty"><p>${__("No calls in progress.")}</p></div>`}
					</section>
				</div>`);
			$root.find("[data-status]").on("click", async function () {
				await v1("set_status", { status: $(this).data("status") });
				render();
			});
			const $dial = $root.find(".ntv-dial");
			$dial.find("button").on("click", () => { const n = $dial.find("input").val(); if (n) V.dial(n); });
			$dial.find("input").on("keydown", (e) => { if (e.key === "Enter") $dial.find("button").click(); });
		};
		render();
		const refresh = frappe.utils.debounce(() => { if (frappe.get_route_str() === "voice-console") render(); }, 1500);
		frappe.realtime.on("neotec_voice_board", refresh);
		setInterval(refresh, 20000);
	};
})();
