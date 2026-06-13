"""IP validation and IP2Location lookup helpers."""

from __future__ import annotations

import ipaddress
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class IPLocation:
    ip: str
    country: str | None = None
    region: str | None = None
    city: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    processed_at: str | None = None
    status: str = "ok"
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_ip(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan"}:
        return None
    if "," in text:
        text = text.split(",", 1)[0].strip()
    return text


def is_valid_ip(value: Any, allow_private: bool = False) -> bool:
    text = normalize_ip(value)
    if not text:
        return False
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    if allow_private:
        return True
    return ip.is_global


def _clean_text_field(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "-" or "unavailable in selected .BIN" in text:
        return None
    return text


def _clean_float_field(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def lookup_ip2location(ip_value: Any, db_path: str | None, allow_private: bool = False) -> IPLocation:
    ip = normalize_ip(ip_value)
    processed_at = datetime.now(timezone.utc).isoformat()

    if not ip or not is_valid_ip(ip, allow_private=allow_private):
        return IPLocation(ip=ip or "", processed_at=processed_at, status="invalid_ip")

    if not db_path:
        return IPLocation(ip=ip, processed_at=processed_at, status="lookup_not_configured")

    try:
        import IP2Location  # type: ignore
    except ImportError:
        return IPLocation(
            ip=ip,
            processed_at=processed_at,
            status="missing_dependency",
            error_message="Install ip2location-python or pass it as a Glue additional Python module.",
        )

    try:
        database = IP2Location.IP2Location(db_path)
        return lookup_ip2location_with_database(ip, database, allow_private=allow_private)
    except Exception as exc:
        return IPLocation(ip=ip, processed_at=processed_at, status="lookup_failed", error_message=str(exc))


def lookup_ip2location_with_database(ip_value: Any, database: Any, allow_private: bool = False) -> IPLocation:
    ip = normalize_ip(ip_value)
    processed_at = datetime.now(timezone.utc).isoformat()

    if not ip or not is_valid_ip(ip, allow_private=allow_private):
        return IPLocation(ip=ip or "", processed_at=processed_at, status="invalid_ip")

    try:
        record = database.get_all(ip)
        return IPLocation(
            ip=ip,
            country=_clean_text_field(getattr(record, "country_long", None)),
            region=_clean_text_field(getattr(record, "region", None)),
            city=_clean_text_field(getattr(record, "city", None)),
            latitude=_clean_float_field(getattr(record, "latitude", None)),
            longitude=_clean_float_field(getattr(record, "longitude", None)),
            processed_at=processed_at,
            status="ok",
        )
    except Exception as exc:
        return IPLocation(ip=ip, processed_at=processed_at, status="lookup_failed", error_message=str(exc))
