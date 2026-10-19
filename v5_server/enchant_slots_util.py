"""Shared normalization for equipment enchantment slots sent to the client."""

import json


def default_slot_list():
    """Return fresh empty slot records for the two client-required slots."""
    return [
        {"id": 1, "effect_list": [], "preview_list": []},
        {"id": 2, "effect_list": [], "preview_list": []},
    ]


def _normalize_effect_list(value):
    if not isinstance(value, (list, tuple)):
        return []

    effects = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        try:
            effect_id = int(entry.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if effect_id <= 0:
            continue
        try:
            level = int(entry.get("level", 1))
        except (TypeError, ValueError):
            level = 1
        effects.append({"id": effect_id, "level": level})
    return effects


def _normalize_preview_list(value):
    if not isinstance(value, (list, tuple)):
        return []

    previews = []
    for preview in value:
        if isinstance(preview, dict) and "effect_list" in preview:
            effects = _normalize_effect_list(preview.get("effect_list"))
        elif isinstance(preview, (list, tuple)):
            effects = _normalize_effect_list(preview)
        elif isinstance(preview, dict) and preview.get("id"):
            effects = _normalize_effect_list([preview])
        else:
            effects = []
        if effects:
            previews.append({"effect_list": effects})
    return previews


def normalize_enchant_slots(value):
    """Normalize DB JSON or a slot list into the client wire shape.

    Valid existing slot IDs, effects, and previews are retained. Invalid records
    are discarded, duplicate slot IDs use the last record, and empty slots 1/2
    are supplied when absent.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = []
    if not isinstance(value, (list, tuple)):
        value = []

    slots_by_id = {}
    for slot in value:
        if not isinstance(slot, dict):
            continue
        try:
            slot_id = int(slot.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if slot_id <= 0:
            continue
        slots_by_id[slot_id] = {
            "id": slot_id,
            "effect_list": _normalize_effect_list(slot.get("effect_list")),
            "preview_list": _normalize_preview_list(slot.get("preview_list")),
        }

    for default_slot in default_slot_list():
        slots_by_id.setdefault(default_slot["id"], default_slot)
    return [slots_by_id[slot_id] for slot_id in sorted(slots_by_id)]
