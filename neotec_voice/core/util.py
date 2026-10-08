"""Small, dependency-free helpers shared across Neotec Voice Hub."""

import json
import re
from datetime import datetime, time, timedelta, timezone

import frappe
from frappe.utils import cint

SETTINGS = "Voice Settings"


def settings():
	return frappe.get_cached_doc(SETTINGS)


def setting(field, default=None):
	value = settings().get(field)
	return default if value in (None, "") else value


def to_dt(value):
	"""Accept epoch (s/ms), ISO-8601 (with or without tz) or Frappe strings and
	return a naive datetime in the system timezone. Never raises."""
	if value in (None, "", "null", "None", 0, "0"):
		return None
	try:
		if isinstance(value, datetime):
			dt = value
		elif isinstance(value, (int, float)) or re.fullmatch(r"\d{9,13}(\.\d+)?", str(value).strip()):
			ts = float(value)
			ts = ts / 1000 if ts > 1e11 else ts
			return _to_system(datetime.fromtimestamp(ts, tz=timezone.utc))
		else:
			from dateutil import parser

			dt = parser.parse(str(value))
		return _to_system(dt) if dt.tzinfo else dt
	except Exception:
		return None


def _to_system(aware):
	from frappe.utils.data import convert_utc_to_system_timezone

	naive_utc = aware.astimezone(timezone.utc).replace(tzinfo=None)
	return convert_utc_to_system_timezone(naive_utc).replace(tzinfo=None)


def as_time(value):
	"""Time fields arrive as timedelta from the DB and str from forms."""
	if value in (None, ""):
		return None
	if isinstance(value, time):
		return value
	if isinstance(value, timedelta):
		return (datetime.min + value).time()
	from frappe.utils import get_time

	return get_time(value)


def seconds(value):
	"""'125', 125, '00:02:05', '2:05' -> 125."""
	if value in (None, "", "null"):
		return None
	text = str(value).strip()
	if ":" in text:
		total = 0
		for part in text.split(":"):
			total = total * 60 + cint(float(part or 0))
		return total
	try:
		return int(float(text))
	except ValueError:
		return None


def dig(obj, path, default=None):
	"""dig({'a': [{'b': 1}]}, 'a.0.b') -> 1. Path may start with '$.'."""
	if not path:
		return default
	cur = obj
	for part in str(path).removeprefix("$.").split("."):
		if isinstance(cur, dict):
			cur = cur.get(part)
		elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
			cur = cur[int(part)]
		else:
			return default
		if cur is None:
			return default
	return cur


def loads(value, default=None):
	if isinstance(value, (dict, list)):
		return value
	try:
		return json.loads(value) if value else default
	except (TypeError, ValueError):
		return default


def split_csv(value):
	return [p.strip() for p in str(value or "").replace("\n", ",").split(",") if p.strip()]


def digits(value):
	return re.sub(r"\D", "", str(value or ""))
