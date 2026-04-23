"""Zone Mapper Integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.util import slugify

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, HomeAssistant, ServiceCall
    from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_CX,
    ATTR_CY,
    ATTR_DATA,
    ATTR_NAME,
    ATTR_POINTS,
    ATTR_ROTATION_DEG,
    ATTR_RX,
    ATTR_RY,
    ATTR_SHAPE,
    ATTR_X_MAX,
    ATTR_X_MIN,
    ATTR_Y_MAX,
    ATTR_Y_MIN,
    CONF_AUTO_CREATE_VIEW,
    COORD_SENSOR_UNIQUE_ID_FMT,
    DATA_LOCATIONS,
    DATA_PLATFORMS_LOADED,
    DEFAULT_AUTO_CREATE_VIEW,
    DOMAIN,
    EVENT_ZONE_UPDATED,
    POLYGON_MAX_POINTS,
    POLYGON_MIN_POINTS,
    PRESENCE_SENSOR_UNIQUE_ID_FMT,
    SEEDED_DEFAULT_VIEW_FLAG,
    SERVICE_UPDATE_ZONE,
    SHAPE_ELLIPSE,
    SHAPE_NONE,
    SHAPE_POLYGON,
    SHAPE_RECT,
    STORE_ENTITIES,
    STORE_ZONES,
    SUPPORTED_SHAPES,
    WARN_ELLIPSE_INVALID,
    WARN_ELLIPSE_NON_POSITIVE,
    WARN_POLY_INSUFFICIENT,
    WARN_POLY_TRUNCATING,
    WARN_RECT_INVALID,
    WARN_RECT_NON_NUM,
)
from .frontend import async_seed_default_view

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema({})}, extra=vol.ALLOW_EXTRA)


def _slugify_location(location: str) -> str:
    return slugify(str(location))


def _get_integration_data(hass: HomeAssistant) -> dict[str, Any]:
    data = hass.data.setdefault(DOMAIN, {})
    data.setdefault(DATA_LOCATIONS, {})
    data.setdefault(DATA_PLATFORMS_LOADED, set())
    return data


def _ensure_location_store(hass: HomeAssistant, location: str) -> dict[str, Any]:
    integration = _get_integration_data(hass)
    locations = integration[DATA_LOCATIONS]
    return locations.setdefault(location, {STORE_ZONES: {}, STORE_ENTITIES: []})


def _parse_sensor_unique_id(unique_id: str) -> tuple[str, int] | None:
    if not unique_id.startswith("zone_mapper_") or "_zone_" not in unique_id:
        return None
    slug_and_zone = unique_id[len("zone_mapper_") :]
    slug, zone_str = slug_and_zone.rsplit("_zone_", 1)
    try:
        zone_id = int(zone_str)
    except (TypeError, ValueError):
        return None
    return slug, zone_id


def _derive_location_name(entry: er.RegistryEntry, fallback_slug: str) -> str:
    name_candidates: Iterable[str | None] = (entry.original_name, entry.name)
    for candidate in name_candidates:
        if not isinstance(candidate, str):
            continue
        if not candidate.startswith("Zone Mapper ") or " Zone " not in candidate:
            continue
        try:
            tail_idx = candidate.rfind(" Zone ")
            location = candidate[len("Zone Mapper ") : tail_idx]
            if location:
                return location
        except Exception:
            _LOGGER.exception("Failed to restore zone data for location '%s'", location)
            continue
    return fallback_slug


def _normalize_entities(entities: Any) -> list[dict[str, str]] | None:
    if entities is None:
        return None
    if not isinstance(entities, list):
        return None
    normalized: list[dict[str, str]] = []
    for pair in entities:
        if not isinstance(pair, dict):
            continue
        x_id = pair.get("x")
        y_id = pair.get("y")
        if isinstance(x_id, str) and isinstance(y_id, str):
            normalized.append({"x": x_id, "y": y_id})
    return normalized


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sanitize_rotation(rotation: Any) -> int | None:
    if rotation is None:
        return None
    try:
        value = round(float(rotation))
    except (TypeError, ValueError):
        return None
    return max(-180, min(180, value))


def _coerce_zone_name(name: Any) -> str | None:
    if name is None:
        return None
    if isinstance(name, str):
        return name
    return str(name)


def _normalize_zone_payload(
    shape: str, data: Any, zone_id: int, location: str
) -> dict[str, Any] | None:
    if shape == SHAPE_NONE or data is None:
        return None
    if shape == SHAPE_RECT:
        return _normalize_rect(data, zone_id, location)
    if shape == SHAPE_ELLIPSE:
        return _normalize_ellipse(data, zone_id, location)
    if shape == SHAPE_POLYGON:
        return _normalize_polygon(data, zone_id, location)
    return None


def _normalize_rect(data: Any, zone_id: int, location: str) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    x_min = _coerce_float(data.get(ATTR_X_MIN))
    x_max = _coerce_float(data.get(ATTR_X_MAX))
    y_min = _coerce_float(data.get(ATTR_Y_MIN))
    y_max = _coerce_float(data.get(ATTR_Y_MAX))
    if x_min is None or x_max is None or y_min is None or y_max is None:
        _LOGGER.warning(WARN_RECT_NON_NUM, zone_id, location)
        return None
    x_min_i = round(x_min)
    x_max_i = round(x_max)
    y_min_i = round(y_min)
    y_max_i = round(y_max)
    if not (x_min_i < x_max_i and y_min_i < y_max_i):
        _LOGGER.warning(WARN_RECT_INVALID, zone_id, location)
        return None
    return {
        **data,
        ATTR_X_MIN: x_min_i,
        ATTR_X_MAX: x_max_i,
        ATTR_Y_MIN: y_min_i,
        ATTR_Y_MAX: y_max_i,
    }


def _normalize_ellipse(data: Any, zone_id: int, location: str) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    cx = _coerce_float(data.get(ATTR_CX))
    cy = _coerce_float(data.get(ATTR_CY))
    rx = _coerce_float(data.get(ATTR_RX))
    ry = _coerce_float(data.get(ATTR_RY))
    if cx is None or cy is None or rx is None or ry is None:
        _LOGGER.warning(WARN_ELLIPSE_INVALID, zone_id, location)
        return None
    if rx <= 0 or ry <= 0:
        _LOGGER.warning(WARN_ELLIPSE_NON_POSITIVE, zone_id, location)
        return None
    cx_i = round(cx)
    cy_i = round(cy)
    rx_i = max(1, round(rx))
    ry_i = max(1, round(ry))
    return {
        ATTR_CX: cx_i,
        ATTR_CY: cy_i,
        ATTR_RX: rx_i,
        ATTR_RY: ry_i,
    }


def _normalize_polygon(data: Any, zone_id: int, location: str) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    points = data.get(ATTR_POINTS)
    if not isinstance(points, list) or len(points) < POLYGON_MIN_POINTS:
        _LOGGER.warning(WARN_POLY_INSUFFICIENT, zone_id, location)
        return None
    total_points = len(points)
    trimmed = list(points[:POLYGON_MAX_POINTS])
    if total_points > POLYGON_MAX_POINTS:
        _LOGGER.warning(
            WARN_POLY_TRUNCATING, zone_id, location, total_points, POLYGON_MAX_POINTS
        )
    normalized_points = []
    for point in trimmed:
        if not isinstance(point, dict):
            continue
        x_float = _coerce_float(point.get("x"))
        y_float = _coerce_float(point.get("y"))
        if x_float is None or y_float is None:
            continue
        x_val = round(x_float)
        y_val = round(y_float)
        normalized_points.append({"x": x_val, "y": y_val})
    if len(normalized_points) < POLYGON_MIN_POINTS:
        _LOGGER.warning(WARN_POLY_INSUFFICIENT, zone_id, location)
        return None
    return {**data, ATTR_POINTS: normalized_points}


def _update_registry_names(
    hass: HomeAssistant, location: str, zone_id: int, zone_name: str
) -> None:
    try:
        registry = er.async_get(hass)
    except HomeAssistantError as exc:
        _LOGGER.debug(
            "Zone Mapper: entity registry not available to rename zone %s at '%s': %s",
            zone_id,
            location,
            exc,
        )
        return

    safe_location = _slugify_location(location)
    sensor_uid = COORD_SENSOR_UNIQUE_ID_FMT.format(
        location=safe_location, zone_id=zone_id
    )
    presence_uid = PRESENCE_SENSOR_UNIQUE_ID_FMT.format(
        location=safe_location, zone_id=zone_id
    )

    sensor_eid = registry.async_get_entity_id("sensor", DOMAIN, sensor_uid)
    if sensor_eid:
        base = f"Zone Mapper {location} Zone {zone_id}"
        new_name = f"{base} - {zone_name}" if zone_name else base
        registry.async_update_entity(sensor_eid, name=new_name)

    presence_eid = registry.async_get_entity_id("binary_sensor", DOMAIN, presence_uid)
    if presence_eid:
        base = f"{location} Zone {zone_id} Presence"
        new_name = f"{base} - {zone_name}" if zone_name else base
        registry.async_update_entity(presence_eid, name=new_name)


def _remove_zone(hass: HomeAssistant, location: str, zone_id: int) -> None:
    store = _ensure_location_store(hass, location)
    store[STORE_ZONES].pop(zone_id, None)
    try:
        registry = er.async_get(hass)
    except HomeAssistantError as exc:
        _LOGGER.debug(
            "Zone Mapper: entity registry not available while deleting zone %s"
            " at '%s': %s",
            zone_id,
            location,
            exc,
        )
        return

    safe_location = _slugify_location(location)
    sensor_uid = COORD_SENSOR_UNIQUE_ID_FMT.format(
        location=safe_location, zone_id=zone_id
    )
    presence_uid = PRESENCE_SENSOR_UNIQUE_ID_FMT.format(
        location=safe_location, zone_id=zone_id
    )

    sensor_eid = registry.async_get_entity_id("sensor", DOMAIN, sensor_uid)
    if sensor_eid:
        registry.async_remove(sensor_eid)

    presence_eid = registry.async_get_entity_id("binary_sensor", DOMAIN, presence_uid)
    if presence_eid:
        registry.async_remove(presence_eid)


def _load_platforms_if_needed(
    hass: HomeAssistant, location: str, config: ConfigType
) -> None:
    integration = _get_integration_data(hass)
    loaded = integration[DATA_PLATFORMS_LOADED]
    if location in loaded:
        return

    store = _ensure_location_store(hass, location)
    discovery_info = {
        "location": location,
        "zones": dict(store[STORE_ZONES]),
    }
    hass.async_create_task(
        async_load_platform(hass, "binary_sensor", DOMAIN, discovery_info, config)
    )
    hass.async_create_task(
        async_load_platform(hass, "sensor", DOMAIN, discovery_info, config)
    )
    loaded.add(location)


def _fire_update_event(hass: HomeAssistant, location: str) -> None:
    hass.bus.async_fire(EVENT_ZONE_UPDATED, {"location": location})


def _build_bootstrap_callback(
    hass: HomeAssistant, config: ConfigType
) -> Callable[[Event | None], Coroutine[Any, Any, None]]:
    async def _bootstrap_from_entity_registry(_event: Event | None = None) -> None:
        try:
            registry = er.async_get(hass)
        except HomeAssistantError as exc:
            _LOGGER.debug(
                "Zone Mapper: entity registry not available at startup: %s", exc
            )
            return

        locations: dict[str, set[int]] = {}
        for entry in registry.entities.values():
            if entry.platform != DOMAIN or entry.domain != "sensor":
                continue
            parsed = _parse_sensor_unique_id(entry.unique_id or "")
            if not parsed:
                continue
            slug, zone_id = parsed
            location_name = _derive_location_name(entry, slug)
            locations.setdefault(location_name, set()).add(zone_id)

        for location_name, zone_ids in locations.items():
            store = _ensure_location_store(hass, location_name)
            for zone_id in zone_ids:
                store[STORE_ZONES].setdefault(zone_id, {})
            _load_platforms_if_needed(hass, location_name, config)

    return _bootstrap_from_entity_registry


def _build_update_zone_handler(
    hass: HomeAssistant, config: ConfigType
) -> Callable[[ServiceCall], Coroutine[Any, Any, None]]:
    async def handle_update_zone(call: ServiceCall) -> None:
        location_raw = call.data.get("location")
        if not isinstance(location_raw, str) or not location_raw.strip():
            _LOGGER.debug(
                "Zone Mapper: rejected update with invalid location: %s", location_raw
            )
            return

        location = location_raw.strip()
        zone_id = call.data.get("zone_id")
        shape = call.data.get("shape")
        data = call.data.get("data")
        entities = _normalize_entities(call.data.get("entities"))
        rotation = _sanitize_rotation(call.data.get(ATTR_ROTATION_DEG))
        zone_name = _coerce_zone_name(call.data.get("name"))
        delete_zone = bool(call.data.get("delete"))

        store = _ensure_location_store(hass, location)

        if rotation is not None:
            store[ATTR_ROTATION_DEG] = rotation

        if entities is not None:
            store[STORE_ENTITIES] = entities

        if delete_zone and zone_id is not None:
            _remove_zone(hass, location, zone_id)
            _fire_update_event(hass, location)
            return

        if zone_id is None or shape is None:
            if zone_id is not None and zone_name is not None:
                zone_entry = store[STORE_ZONES].get(zone_id)
                if isinstance(zone_entry, dict):
                    zone_entry[ATTR_NAME] = zone_name
                    _update_registry_names(hass, location, zone_id, zone_name)
            _fire_update_event(hass, location)
            return

        if shape not in SUPPORTED_SHAPES:
            _LOGGER.debug(
                "Zone Mapper: unsupported shape '%s' for location '%s'",
                shape,
                location,
            )
            return

        normalized_data = _normalize_zone_payload(shape, data, zone_id, location)
        zone_entry: dict[str, Any] = {
            ATTR_SHAPE: shape,
            ATTR_DATA: normalized_data,
        }

        if zone_name is not None:
            zone_entry[ATTR_NAME] = zone_name
        else:
            existing = store[STORE_ZONES].get(zone_id, {})
            if isinstance(existing, dict) and isinstance(existing.get(ATTR_NAME), str):
                zone_entry[ATTR_NAME] = existing[ATTR_NAME]

        store[STORE_ZONES][zone_id] = zone_entry

        if zone_name is not None:
            _update_registry_names(hass, location, zone_id, zone_name)

        _load_platforms_if_needed(hass, location, config)
        _fire_update_event(hass, location)

    return handle_update_zone


UPDATE_ZONE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required("location"): cv.string,
        vol.Optional("zone_id"): cv.positive_int,
        vol.Optional("shape"): vol.In(list(SUPPORTED_SHAPES)),
        vol.Optional("data"): vol.Any(None, dict),
        vol.Optional(ATTR_ROTATION_DEG): vol.Coerce(float),
        vol.Optional("name"): cv.string,
        vol.Optional("delete"): cv.boolean,
        vol.Optional("entities"): vol.All(
            cv.ensure_list,
            [
                vol.Schema(
                    {
                        vol.Required("x"): cv.entity_id,
                        vol.Required("y"): cv.entity_id,
                    },
                    extra=vol.ALLOW_EXTRA,
                )
            ],
        ),
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register Zone Mapper services and bootstrap stored entities."""
    _get_integration_data(hass)
    hass.bus.async_listen_once(
        EVENT_HOMEASSISTANT_STARTED, _build_bootstrap_callback(hass, config)
    )
    if not hass.services.has_service(DOMAIN, SERVICE_UPDATE_ZONE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_UPDATE_ZONE,
            _build_update_zone_handler(hass, config),
            schema=UPDATE_ZONE_SERVICE_SCHEMA,
        )
    return True


def _auto_create_view_enabled(entry: ConfigEntry) -> bool:
    value = entry.options.get(CONF_AUTO_CREATE_VIEW, DEFAULT_AUTO_CREATE_VIEW)
    return bool(value)


async def _schedule_view_seeding(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Seed the default Lovelace view once HA is fully started."""
    if entry.data.get(SEEDED_DEFAULT_VIEW_FLAG):
        return
    if not _auto_create_view_enabled(entry):
        return

    async def _run(_event: Event | None = None) -> None:
        if entry.data.get(SEEDED_DEFAULT_VIEW_FLAG):
            return
        if not _auto_create_view_enabled(entry):
            return
        seeded = await async_seed_default_view(hass)
        if seeded:
            hass.config_entries.async_update_entry(
                entry,
                data={**entry.data, SEEDED_DEFAULT_VIEW_FLAG: True},
            )

    if getattr(hass, "is_running", False):
        hass.async_create_task(_run(None))
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _run)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Set up Zone Mapper from a config entry.

    Registers the update service, bootstraps any restored entities, and (once)
    seeds a default Lovelace view so new users see the card immediately
    (assuming the zone-mapper-card frontend is installed via HACS).
    """
    _get_integration_data(hass)

    # Ensure service is registered only once across YAML and UI setups.
    if not hass.services.has_service(DOMAIN, SERVICE_UPDATE_ZONE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_UPDATE_ZONE,
            _build_update_zone_handler(hass, {}),
            schema=UPDATE_ZONE_SERVICE_SCHEMA,
        )

    # Bootstrap entities either at startup or immediately if HA is already running.
    bootstrap_cb = _build_bootstrap_callback(hass, {})
    if getattr(hass, "is_running", False):
        hass.async_create_task(bootstrap_cb(None))
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, bootstrap_cb)

    await _schedule_view_seeding(hass, entry)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Unload a config entry for Zone Mapper.

    The integration stores state in memory and uses a shared service; nothing to
    unload per-entry at this time.
    """
    _ = (hass, entry)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """
    Remove a config entry.

    Since this integration uses legacy platform loading which doesn't associate
    entities with the config entry, we must manually clean them up from the registry.
    """
    _ = entry
    registry = er.async_get(hass)
    entities_to_remove = [
        entry.entity_id
        for entry in registry.entities.values()
        if entry.platform == DOMAIN
    ]

    for entity_id in entities_to_remove:
        registry.async_remove(entity_id)
