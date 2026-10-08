// Neotec Voice Hub — browser softphone.
// SIP-over-WSS (Asterisk, FreeSWITCH, any WebRTC SIP server) via JsSIP, or Twilio Voice SDK.
frappe.provide("neotec.voice");

(() => {
	const V = neotec.voice;
	const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
	let cfg = null;
	let ua = null; // JsSIP UA
	let device = null; // Twilio Device
	let current = null; // active session / call
	let incomingDialog = null;
	const audio = new Audio();
	audio.autoplay = true;

	const load = (url) =>
		new Promise((resolve, reject) => {
			if (!url) return reject(new Error("no library url"));
			const s = document.createElement("script");
			s.src = url;
			s.onload = resolve;
			s.onerror = () => reject(new Error(`could not load ${url}`));
			document.head.appendChild(s);
		});

	const indicator = (state) => {
		$(".ntv-nav button").attr("data-softphone", state).attr("title", __("Softphone: {0}", [__(state)]));
	};

	const ask_answer = (label, onAnswer, onDecline) => {
		incomingDialog = frappe.confirm(
			__("Incoming call from {0}. Answer?", [`<b>${esc(label)}</b>`]),
			onAnswer,
			onDecline
		);
	};

	// --------------------------------------------------------- SIP (JsSIP)
	const start_sip = async () => {
		if (!window.JsSIP) await load(cfg.lib_url);
		const socket = new JsSIP.WebSocketInterface(cfg.wss_url);
		ua = new JsSIP.UA({
			sockets: [socket],
			uri: `sip:${cfg.username}@${cfg.domain}`,
			password: cfg.password,
			display_name: cfg.display_name,
			register: true,
			session_timers: false,
		});
		ua.on("registered", () => indicator("ready"));
		ua.on("unregistered", () => indicator("offline"));
		ua.on("registrationFailed", (e) => {
			indicator("error");
			frappe.show_alert({ message: __("Softphone registration failed: {0}", [esc(e.cause)]), indicator: "red" }, 8);
		});
		ua.on("newRTCSession", ({ session, originator }) => {
			current = session;
			const attach = (pc) => pc && pc.addEventListener("track", (ev) => { audio.srcObject = ev.streams[0]; });
			session.on("peerconnection", (e) => attach(e.peerconnection));
			if (session.connection) attach(session.connection);
			const done = () => { if (current === session) current = null; indicator("ready"); };
			session.on("ended", done);
			session.on("failed", done);
			if (originator === "remote") {
				const label = session.remote_identity.display_name || session.remote_identity.uri.user;
				session.on("failed", () => incomingDialog && incomingDialog.hide());
				ask_answer(label, () => session.answer({ mediaConstraints: { audio: true, video: false }, pcConfig: pc_config() }),
					() => session.terminate());
			}
			indicator("in call");
		});
		ua.start();
	};

	const pc_config = () => ({ iceServers: [{ urls: cfg.stun || "stun:stun.l.google.com:19302" }] });

	// ------------------------------------------------------ Twilio Voice SDK
	const start_twilio = async () => {
		if (!window.Twilio || !window.Twilio.Device) await load(cfg.lib_url);
		device = new Twilio.Device(cfg.token, { codecPreferences: ["opus", "pcmu"], closeProtection: true });
		device.on("registered", () => indicator("ready"));
		device.on("error", (e) => { indicator("error"); frappe.show_alert({ message: esc(e.message), indicator: "red" }, 8); });
		device.on("tokenWillExpire", async () => {
			const fresh = await frappe.xcall("neotec_voice.api.v1.softphone_config");
			if (fresh && fresh.token) device.updateToken(fresh.token);
		});
		device.on("incoming", (call) => {
			current = call;
			call.on("disconnect", () => { current = null; indicator("ready"); });
			call.on("cancel", () => { current = null; incomingDialog && incomingDialog.hide(); });
			ask_answer(call.parameters.From, () => call.accept(), () => call.reject());
		});
		await device.register();
	};

	V.softphone = {
		async start() {
			try {
				cfg = await frappe.xcall("neotec_voice.api.v1.softphone_config");
				if (!cfg) return;
				indicator("connecting");
				if (cfg.kind === "twilio") await start_twilio();
				else if (cfg.kind === "sip" && cfg.wss_url && cfg.username) await start_sip();
			} catch (e) {
				indicator("error");
				console.warn("Neotec softphone", e);
			}
		},
		ready() {
			return !!((ua && ua.isRegistered()) || (device && device.state === "registered"));
		},
		active() {
			return !!current;
		},
		async call(number, ntCall) {
			if (ua) {
				current = ua.call(`sip:${number}@${cfg.domain}`, {
					mediaConstraints: { audio: true, video: false },
					pcConfig: pc_config(),
					extraHeaders: ntCall ? [`X-NT-Call: ${ntCall}`] : [],
				});
			} else if (device) {
				current = await device.connect({ params: { To: number, nt: ntCall || "" } });
				current.on("disconnect", () => { current = null; indicator("ready"); });
			}
			indicator("in call");
		},
		hangup() {
			if (!current) return;
			if (ua) current.terminate();
			else current.disconnect();
			current = null;
		},
		mute(on) {
			if (!current) return;
			if (ua) (on ? current.mute() : current.unmute());
			else current.mute(!!on);
		},
	};
})();
