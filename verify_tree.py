#!/usr/bin/env python3
"""Structural guard for Neotec Voice Hub. Run before every push:  python3 verify_tree.py"""

import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "neotec_voice")
MODULE = os.path.join(APP, "neotec_voice")
CORE_DOCTYPES = {"User", "DocType", "Currency", "Dynamic Link", "Role", "File", "Contact", "Customer"}
FORBIDDEN = [r"(?i)alphax", r"\btelephony\b", r"Frappe Technologies", r"\bTP (Call Log|Telephony|OTP|SMS)"]
errors = []


def err(msg):
	errors.append(msg)


# 1. every python file compiles
for base, _dirs, files in os.walk(APP):
	for f in files:
		if f.endswith(".py"):
			p = os.path.join(base, f)
			try:
				ast.parse(open(p, encoding="utf-8").read(), p)
			except SyntaxError as e:
				err(f"syntax: {p}: {e}")

# 2. doctypes
doctypes, tables = {}, set()
ddir = os.path.join(MODULE, "doctype")
for folder in sorted(os.listdir(ddir)):
	path = os.path.join(ddir, folder)
	if not os.path.isdir(path):
		continue
	for need in ("__init__.py", f"{folder}.json", f"{folder}.py"):
		if not os.path.exists(os.path.join(path, need)):
			err(f"doctype {folder}: missing {need}")
	try:
		doc = json.load(open(os.path.join(path, f"{folder}.json"), encoding="utf-8"))
	except Exception as e:
		err(f"doctype {folder}: bad json {e}")
		continue
	if doc["name"].lower().replace(" ", "_") != folder:
		err(f"doctype {folder}: name mismatch {doc['name']}")
	if doc.get("module") != "Neotec Voice":
		err(f"doctype {folder}: module {doc.get('module')}")
	doctypes[doc["name"]] = doc
	if doc.get("istable"):
		tables.add(doc["name"])
	names = [f["fieldname"] for f in doc["fields"]]
	if len(names) != len(set(names)):
		err(f"doctype {folder}: duplicate fieldnames")
	if names != doc.get("field_order"):
		err(f"doctype {folder}: field_order mismatch")
	cls = doc["name"].replace(" ", "")
	if f"class {cls}(" not in open(os.path.join(path, f"{folder}.py"), encoding="utf-8").read():
		err(f"doctype {folder}: controller class {cls} missing")

for name, doc in doctypes.items():
	fields = {f["fieldname"]: f for f in doc["fields"]}
	for f in doc["fields"]:
		opt = f.get("options")
		if f["fieldtype"] == "Link" and opt not in doctypes and opt not in CORE_DOCTYPES:
			err(f"{name}.{f['fieldname']}: Link to unknown {opt}")
		if f["fieldtype"] == "Table" and opt not in tables and opt != "Dynamic Link":
			err(f"{name}.{f['fieldname']}: Table of non-child {opt}")
		if f["fieldtype"] == "Dynamic Link" and opt not in fields:
			err(f"{name}.{f['fieldname']}: Dynamic Link options field {opt} missing")
	if doc.get("title_field") and doc["title_field"] not in fields:
		err(f"{name}: title_field {doc['title_field']} missing")

# 3. providers <-> pages
init = open(os.path.join(APP, "providers", "__init__.py"), encoding="utf-8").read()
mods = re.findall(r'"(\w+)",', init.split("BUILTIN_MODULES")[1].split(")")[0])
for mod in mods:
	src = open(os.path.join(APP, "providers", f"{mod}.py"), encoding="utf-8").read()
	m = re.search(r'^\s+slug = "([\w]+)"', src, re.M)
	if not m:
		err(f"provider {mod}: no slug")
		continue
	page = "voice-" + m.group(1).replace("_", "-")
	pdir = os.path.join(MODULE, "page", page.replace("-", "_"))
	for ext in ("json", "js"):
		if not os.path.exists(os.path.join(pdir, f"{page.replace('-', '_')}.{ext}")):
			err(f"provider {mod}: page {page} missing .{ext}")
	if "@register" not in src:
		err(f"provider {mod}: not registered")
	if f'frappe.pages["{page}"]' not in open(os.path.join(pdir, f"{page.replace('-', '_')}.js"), encoding="utf-8").read():
		err(f"page {page}: js key mismatch")

# 4. hooks dotted paths resolve to defined functions
hooks = open(os.path.join(APP, "hooks.py"), encoding="utf-8").read()
for path in set(re.findall(r'"(neotec_voice\.[\w.]+)"', hooks)):
	mod, _sep, fn = path.rpartition(".")
	f = os.path.join(ROOT, *mod.split(".")) + ".py"
	if not os.path.exists(f) or not re.search(rf"^def {fn}\(", open(f, encoding="utf-8").read(), re.M):
		err(f"hooks: {path} does not resolve")
for asset in re.findall(r'"/assets/neotec_voice/([^"]+)"', hooks):
	if not os.path.exists(os.path.join(APP, "public", asset)):
		err(f"hooks: asset {asset} missing")

# 5. whitelisted API referenced from JS exists
api_src = {m: open(os.path.join(APP, "api", f"{m}.py"), encoding="utf-8").read() for m in ("v1", "setup", "webhook")}
for base, _dirs, files in os.walk(APP):
	for f in files:
		if f.endswith(".js"):
			js = open(os.path.join(base, f), encoding="utf-8").read()
			for mod, fn in re.findall(r"neotec_voice\.api\.(v1|setup)\.(\w+)", js):
				if not re.search(rf"^def {fn}\(", api_src[mod], re.M):
					err(f"{f}: calls missing api.{mod}.{fn}")
			for fn in re.findall(r'\b(?:setup)\("(\w+)"', js):
				if not re.search(rf"^def {fn}\(", api_src["setup"], re.M):
					err(f"{f}: setup('{fn}') missing")
			for fn in re.findall(r'\b(?:v1|api)\("(\w+)"', js):
				if not re.search(rf"^def {fn}\(", api_src["v1"], re.M):
					err(f"{f}: v1('{fn}') missing")

# 6. original-work guard: no third-party product branding in shipped files
for base, _dirs, files in os.walk(ROOT):
	if ".git" in base:
		continue
	for f in files:
		if f.endswith((".py", ".js", ".json", ".md", ".css", ".csv", ".toml")) and f != "verify_tree.py":
			text = open(os.path.join(base, f), encoding="utf-8").read()
			for pat in FORBIDDEN:
				if re.search(pat, text):
					err(f"branding: {os.path.relpath(os.path.join(base, f), ROOT)} matches {pat}")

if errors:
	print("\n".join(f"✗ {e}" for e in errors))
	sys.exit(1)
print(f"✓ tree OK — {len(doctypes)} doctypes, {len(mods)} providers, pages and hooks consistent")
