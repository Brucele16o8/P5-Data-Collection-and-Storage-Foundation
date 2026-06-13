"""Glamira product crawler and react_data parser."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse


BASIC_REACT_DATA_FIELDS = [
    "product_id",
    "name",
    "sku",
    "attribute_set_id",
    "attribute_set",
    "type_id",
    "price",
    "min_price",
    "max_price",
    "min_price_format",
    "max_price_format",
    "gold_weight",
    "none_metal_weight",
    "fixed_silver_weight",
    "material_design",
    "qty",
    "collection",
    "collection_id",
    "product_type",
    "product_type_value",
    "category",
    "category_name",
    "store_code",
]

DEFAULT_FALLBACK_DOMAINS = [
    "www.glamira.com.au",
    "www.glamira.com",
    "www.glamira.co.uk",
    "www.glamira.ca",
    "www.glamira.ie",
    "www.glamira.de",
    "www.glamira.fr",
    "www.glamira.it",
    "www.glamira.pl",
    "www.glamira.vn",
]

ENGLISH_STORE_DOMAINS = {
    "www.glamira.com.au",
    "www.glamira.com",
    "www.glamira.co.uk",
    "www.glamira.ca",
    "www.glamira.ie",
}


@dataclass(frozen=True)
class ProductInfo:
    requested_product_id: str
    product_id: str | None = None
    source_url: str | None = None
    original_url: str | None = None
    resolved_url: str | None = None
    country_store: str | None = None
    crawl_url_strategy: str | None = None
    product_name: str | None = None
    name: str | None = None
    sku: str | None = None
    category: Any = None
    category_name: str | None = None
    price: Any = None
    currency: str | None = None
    store_code: str | None = None
    active: bool = False
    scraped_at: str | None = None
    status: str = "not_found_in_configured_stores"
    failure_reason: str | None = None
    error_message: str | None = None
    react_data_basic: dict[str, Any] = field(default_factory=dict)
    react_data: dict[str, Any] = field(default_factory=dict)
    attempts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", value).strip()
    return text or None


def _country_store_from_url(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url).netloc.lower()
    if host.startswith("www.glamira."):
        return host.removeprefix("www.glamira.")
    if host.startswith("glamira."):
        return host.removeprefix("glamira.")
    return host or None


def _catalog_url(domain: str, product_id: str) -> str:
    return f"https://{domain}/catalog/product/view/id/{product_id}"


def build_product_url_attempts(
    product_id: str,
    source_url: str,
    fallback_domains: list[str] | None = None,
) -> list[dict[str, str]]:
    """Return URL attempts in priority order for one product."""
    fallback_domains = fallback_domains or DEFAULT_FALLBACK_DOMAINS
    attempts: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(url: str | None, strategy: str) -> None:
        if not url or url in seen:
            return
        seen.add(url)
        attempts.append({"url": url, "strategy": strategy})

    add(source_url, "original_url")

    source_host = urlparse(source_url).netloc.lower()
    if source_host in ENGLISH_STORE_DOMAINS:
        add(_catalog_url(source_host, product_id), "source_domain_product_id_url")

    for domain in fallback_domains:
        add(_catalog_url(domain, product_id), "fallback_domain_product_id_url")

    if source_host and source_host not in ENGLISH_STORE_DOMAINS:
        add(_catalog_url(source_host, product_id), "source_domain_product_id_url")

    return attempts


def _extract_balanced_object(text: str, start_index: int) -> str | None:
    depth = 0
    in_string = False
    quote = ""
    escaped = False

    for index in range(start_index, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            continue

        if char in {'"', "'"}:
            in_string = True
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start_index : index + 1]
    return None


def extract_react_data(html: str) -> dict[str, Any] | None:
    """Extract the JSON object assigned to var react_data from page HTML."""
    match = re.search(r"(?:var\s+)?react_data\s*=", html)
    if not match:
        return None

    object_start = html.find("{", match.end())
    if object_start == -1:
        return None

    decoder = json.JSONDecoder()
    try:
        payload, _ = decoder.raw_decode(html[object_start:])
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        balanced = _extract_balanced_object(html, object_start)
        if not balanced:
            return None
        try:
            payload = json.loads(balanced)
            return payload if isinstance(payload, dict) else None
        except json.JSONDecodeError:
            return None


def _basic_react_data(react_data: dict[str, Any]) -> dict[str, Any]:
    return {field_name: react_data.get(field_name) for field_name in BASIC_REACT_DATA_FIELDS if field_name in react_data}


def _product_ids_match(requested_product_id: str, react_data: dict[str, Any]) -> bool:
    actual = react_data.get("product_id")
    return actual is not None and str(actual).strip() == str(requested_product_id).strip()


def _has_useful_product_data(react_data: dict[str, Any]) -> bool:
    return any(react_data.get(field_name) for field_name in ("name", "sku", "store_code"))


def _meta_title(html: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    return _clean_text(match.group(1)) if match else None


def extract_product_info_from_html(
    product_id: str,
    source_url: str,
    html: str,
    status_code: int = 200,
    resolved_url: str | None = None,
    crawl_url_strategy: str | None = None,
    original_url: str | None = None,
    attempts: list[dict[str, Any]] | None = None,
) -> ProductInfo:
    scraped_at = datetime.now(timezone.utc).isoformat()
    react_data = extract_react_data(html) or {}
    react_data_basic = _basic_react_data(react_data)
    matches_requested_id = _product_ids_match(product_id, react_data)
    active = status_code == 200 and matches_requested_id and _has_useful_product_data(react_data)

    name = react_data.get("name")
    status = "ok" if active else f"http_{status_code}"
    failure_reason = None
    if status_code != 200:
        failure_reason = f"http_{status_code}"
    elif not react_data:
        failure_reason = "react_data_not_found"
    elif not matches_requested_id:
        failure_reason = "product_id_mismatch"
    elif not _has_useful_product_data(react_data):
        failure_reason = "missing_basic_product_fields"

    return ProductInfo(
        requested_product_id=str(product_id),
        product_id=str(react_data.get("product_id")) if react_data.get("product_id") is not None else None,
        source_url=source_url,
        original_url=original_url or source_url,
        resolved_url=resolved_url or source_url,
        country_store=_country_store_from_url(resolved_url or source_url),
        crawl_url_strategy=crawl_url_strategy,
        product_name=name or _meta_title(html),
        name=name,
        sku=react_data.get("sku"),
        category=react_data.get("category"),
        category_name=react_data.get("category_name"),
        price=react_data.get("price"),
        currency=react_data.get("currency") or react_data.get("currency_code"),
        store_code=react_data.get("store_code"),
        active=active,
        scraped_at=scraped_at,
        status=status,
        failure_reason=failure_reason,
        react_data_basic=react_data_basic,
        react_data=react_data,
        attempts=attempts or [],
    )


def _request_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
    }


def fetch_product_info(
    product_id: str,
    source_url: str,
    timeout_seconds: int = 20,
    fallback_domains: list[str] | None = None,
) -> ProductInfo:
    scraped_at = datetime.now(timezone.utc).isoformat()
    attempts: list[dict[str, Any]] = []
    last_error: str | None = None

    try:
        import requests

        session = requests.Session()
        for attempt in build_product_url_attempts(product_id, source_url, fallback_domains):
            url = attempt["url"]
            strategy = attempt["strategy"]
            try:
                response = session.get(
                    url,
                    timeout=timeout_seconds,
                    headers=_request_headers(),
                    allow_redirects=True,
                )
                react_data = extract_react_data(response.text)
                matches_requested_id = bool(react_data and _product_ids_match(product_id, react_data))
                has_data = bool(react_data and _has_useful_product_data(react_data))
                attempt_result = {
                    "url": url,
                    "resolved_url": response.url,
                    "strategy": strategy,
                    "status_code": response.status_code,
                    "react_data_found": react_data is not None,
                    "product_id_matches": matches_requested_id,
                }
                attempts.append(attempt_result)

                if response.status_code == 200 and matches_requested_id and has_data:
                    return extract_product_info_from_html(
                        product_id=product_id,
                        source_url=url,
                        html=response.text,
                        status_code=response.status_code,
                        resolved_url=response.url,
                        crawl_url_strategy=strategy,
                        original_url=source_url,
                        attempts=attempts,
                    )
            except Exception as exc:
                last_error = str(exc)
                attempts.append(
                    {
                        "url": url,
                        "strategy": strategy,
                        "status": "request_failed",
                        "error_message": last_error,
                    }
                )

        return ProductInfo(
            requested_product_id=str(product_id),
            source_url=source_url,
            original_url=source_url,
            active=False,
            scraped_at=scraped_at,
            status="not_found_in_configured_stores",
            failure_reason="not_found_in_configured_stores",
            error_message=last_error,
            attempts=attempts,
        )
    except Exception as exc:
        return ProductInfo(
            requested_product_id=str(product_id),
            source_url=source_url,
            original_url=source_url,
            active=False,
            scraped_at=scraped_at,
            status="crawl_failed",
            failure_reason="crawl_failed",
            error_message=str(exc),
            attempts=attempts,
        )
