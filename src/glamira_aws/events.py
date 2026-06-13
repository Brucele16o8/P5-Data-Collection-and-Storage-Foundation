"""Rules for extracting product URL candidates from Glamira event records."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

PRODUCT_CURRENT_URL_EVENTS = {
    "view_product_detail",
    "select_product_option",
    "select_product6_option_quality",
    "add_to_cart_action",
    "product_detail_recommendation_visible",
    "product_detail_recommendation_noticed",
}

RECOMMEND_CLICK_EVENT = "product_view_all_recommend_clicked"
PRODUCT_EVENTS = PRODUCT_CURRENT_URL_EVENTS | {RECOMMEND_CLICK_EVENT}

EVENT_TYPE_FIELDS = ("event_type", "event", "event_name", "collection", "action")
PRODUCT_ID_FIELDS = ("product_id", "productid", "product")
VIEWING_PRODUCT_ID_FIELDS = ("viewing_product_id", "viewingProductId")
CURRENT_URL_FIELDS = ("current_url", "currentUrl", "url")
REFERRER_URL_FIELDS = ("referrer_url", "referrerUrl", "referer_url", "referer")
EVENT_TIME_FIELDS = ("event_time", "time_stamp", "timestamp", "created_at", "time", "datetime")


def first_non_empty(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() not in {"none", "null", "nan"}:
            return text
    return None


def get_first(record: dict[str, Any], fields: tuple[str, ...]) -> str | None:
    return first_non_empty(*(record.get(field) for field in fields))


def get_event_type(record: dict[str, Any]) -> str | None:
    event_type = get_first(record, EVENT_TYPE_FIELDS)
    return event_type.lower() if event_type else None


def get_event_time(record: dict[str, Any]) -> str | None:
    return get_first(record, EVENT_TIME_FIELDS)


def build_product_url_candidate(record: dict[str, Any]) -> dict[str, Any] | None:
    """Return a normalized product URL candidate from one event record."""
    event_type = get_event_type(record)
    if event_type not in PRODUCT_EVENTS:
        return None

    if event_type == RECOMMEND_CLICK_EVENT:
        product_id = get_first(record, VIEWING_PRODUCT_ID_FIELDS)
        candidate_url = get_first(record, REFERRER_URL_FIELDS)
        url_source_field = "referrer_url"
    else:
        product_id = first_non_empty(
            get_first(record, PRODUCT_ID_FIELDS),
            get_first(record, VIEWING_PRODUCT_ID_FIELDS),
        )
        candidate_url = get_first(record, CURRENT_URL_FIELDS)
        url_source_field = "current_url"

    if not product_id or not candidate_url:
        return None

    return {
        "product_id": product_id,
        "candidate_url": candidate_url,
        "source_event_type": event_type,
        "url_source_field": url_source_field,
        "event_time": get_event_time(record),
    }


def aggregate_product_url_candidates(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate event-level candidates into ranked product URL candidates."""
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    for record in records:
        candidate = build_product_url_candidate(record)
        if not candidate:
            continue
        key = (
            candidate["product_id"],
            candidate["candidate_url"],
            candidate["source_event_type"],
            candidate["url_source_field"],
        )
        item = grouped.setdefault(
            key,
            {
                "product_id": candidate["product_id"],
                "candidate_url": candidate["candidate_url"],
                "source_event_type": candidate["source_event_type"],
                "url_source_field": candidate["url_source_field"],
                "event_count": 0,
                "first_seen_at": None,
                "last_seen_at": None,
            },
        )
        item["event_count"] += 1
        event_time = candidate.get("event_time")
        if event_time:
            item["first_seen_at"] = min(filter(None, [item["first_seen_at"], event_time]))
            item["last_seen_at"] = max(filter(None, [item["last_seen_at"], event_time]))

    by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in grouped.values():
        by_product[item["product_id"]].append(item)

    ranked: list[dict[str, Any]] = []
    for product_id, items in by_product.items():
        ordered = sorted(
            items,
            key=lambda row: (int(row["event_count"]), str(row.get("last_seen_at") or "")),
            reverse=True,
        )
        for rank, item in enumerate(ordered, start=1):
            item["url_rank"] = rank
            ranked.append(item)

    return sorted(ranked, key=lambda row: (row["product_id"], row["url_rank"]))
