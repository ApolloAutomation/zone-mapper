"""Sensor platform for Zone Mapper to store zone coordinates."""

import logging
from collections.abc import Mapping
from contextlib import suppress
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import slugify

from .const import (
    ATTR_DATA,
    ATTR_INPUT_UNITS,
    ATTR_NAME,
    ATTR_ROTATION_DEG,
    ATTR_SHAPE,
    COORD_SENSOR_UNIQUE_ID_FMT,
    DATA_LOCATIONS,
    DOMAIN,
    EVENT_ZONE_UPDATED,
    STORE_ENTITIES,
    STORE_ZONES,
    SUPPORTED_INPUT_UNITS,
)

_LOGGER = logging.getLogger(__name__)


def _slugify_location(location: str) -> str:
    return slugify(str(location))


async def async_setup_platform(
    hass: HomeAssistant,
    _config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the Zone Mapper coordinate sensors based on discovery info."""
    if discovery_info is None:
        return

    location_name = discovery_info.get("location")
    if not isinstance(location_name, str) or not location_name:
        _LOGGER.debug(
            "Zone Mapper discovery info missing location name: %s",
            discovery_info,
        )
        return

    zones = discovery_info.get("zones", {})

    added_zone_ids: set[int] = set()

    def _coerce_zone_id(raw_zone_id: Any) -> int | None:
        try:
            return int(raw_zone_id)
        except (TypeError, ValueError):
            _LOGGER.debug("Skipping non-integer zone id: %s", raw_zone_id)
            return None

    def build_sensors_from_zones(zones_map: Mapping[Any, Any]) -> None:
        # Drop any zone ids we previously added that no longer exist so they can be
        # recreated later.
        current_zone_ids: set[int] = set()
        new_entities: list[ZoneCoordsSensor] = []

        for raw_zone_id in zones_map:
            zone_id = _coerce_zone_id(raw_zone_id)
            if zone_id is None:
                continue
            current_zone_ids.add(zone_id)
            if zone_id not in added_zone_ids:
                new_entities.append(ZoneCoordsSensor(hass, location_name, zone_id))
                added_zone_ids.add(zone_id)

        if new_entities:
            async_add_entities(new_entities, update_before_add=True)

        for stale_id in list(added_zone_ids):
            if stale_id not in current_zone_ids:
                added_zone_ids.discard(stale_id)

    if isinstance(zones, Mapping):
        build_sensors_from_zones(zones)

    @callback
    def handle_zone_update(event: Event) -> None:
        if event.data.get("location") != location_name:
            return
        integration = hass.data.get(DOMAIN, {})
        locations = integration.get(DATA_LOCATIONS, {})
        device_data = locations.get(location_name, {})
        latest_zones: Any = None
        if isinstance(device_data, Mapping):
            latest_zones = device_data.get(STORE_ZONES, {})
        if isinstance(latest_zones, Mapping):
            build_sensors_from_zones(latest_zones)

    hass.bus.async_listen(EVENT_ZONE_UPDATED, handle_zone_update)


def _normalize_entity_pairs(pairs: Any) -> list[dict[str, str]]:
    if not isinstance(pairs, list):
        return []
    normalized: list[dict[str, str]] = []
    for pair in pairs:
        if not isinstance(pair, Mapping):
            continue
        x_id = pair.get("x")
        y_id = pair.get("y")
        if isinstance(x_id, str) and isinstance(y_id, str):
            normalized.append({"x": x_id, "y": y_id})
    return normalized


class ZoneCoordsSensor(RestoreEntity, SensorEntity):
    """Representation of a sensor that stores a zone's coordinates in its attributes."""

    def __init__(self, hass: HomeAssistant, location_name: str, zone_id: int) -> None:
        """Initialize the sensor."""
        self.hass = hass
        self._location_name = location_name
        self._zone_id = zone_id
        self._attr_name = f"Zone Mapper {location_name} Zone {zone_id}"
        safe_location = _slugify_location(location_name)
        self._attr_unique_id = COORD_SENSOR_UNIQUE_ID_FMT.format(
            location=safe_location, zone_id=zone_id
        )
        self._attr_should_poll = False
        self._coords: dict[str, Any] = {ATTR_SHAPE: None, ATTR_DATA: None}
        self._entities: list[dict[str, str]] = []

    @property
    def state(self) -> int:
        """Return the state of the sensor (the zone ID)."""
        return self._zone_id

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        # Persist both coords and tracked entities on the sensor so HA restores them
        # Include rotation (if any) from location data
        integration = self.hass.data.get(DOMAIN)
        base: dict[str, Any] = {**self._coords, "entities": self._entities}

        if not isinstance(integration, Mapping):
            return base

        locations = integration.get(DATA_LOCATIONS, {})
        if isinstance(locations, Mapping):
            loc = locations.get(self._location_name, {})
        else:
            loc = {}
        if not isinstance(loc, Mapping):
            return base

        rotation = loc.get(ATTR_ROTATION_DEG)
        if rotation is not None:
            base[ATTR_ROTATION_DEG] = rotation

        input_units = loc.get(ATTR_INPUT_UNITS)
        if input_units in SUPPORTED_INPUT_UNITS:
            base[ATTR_INPUT_UNITS] = input_units

        zones = loc.get(STORE_ZONES, {})
        zone_def = zones.get(self._zone_id) if isinstance(zones, Mapping) else None
        name = zone_def.get(ATTR_NAME) if isinstance(zone_def, Mapping) else None
        if isinstance(name, str) and name:
            base[ATTR_NAME] = name

        return base

    @property
    def icon(self) -> str:
        """Return the icon to use in the frontend."""
        return "mdi:vector-rectangle"

    async def async_added_to_hass(self) -> None:
        """Register callbacks when entity is added and restore last known state."""
        # Listen for zone updates
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_ZONE_UPDATED, self._handle_zone_update)
        )

        # Ensure base data structure exists
        integration = self.hass.data.setdefault(DOMAIN, {})
        locations = integration.setdefault(DATA_LOCATIONS, {})
        store = locations.setdefault(
            self._location_name, {STORE_ZONES: {}, STORE_ENTITIES: []}
        )

        # Try to restore previous attributes (shape/data/entities/rotation)
        last_state = await self.async_get_last_state()
        if last_state and isinstance(last_state.attributes, dict):
            shape = last_state.attributes.get(ATTR_SHAPE)
            data = last_state.attributes.get(ATTR_DATA)
            entities = _normalize_entity_pairs(last_state.attributes.get("entities"))
            rotation = last_state.attributes.get(ATTR_ROTATION_DEG)
            input_units = last_state.attributes.get(ATTR_INPUT_UNITS)
            zname = last_state.attributes.get(ATTR_NAME)

            # Seed hass.data so binary sensors can evaluate immediately
            if shape is not None:
                zentry = {ATTR_SHAPE: shape, ATTR_DATA: data}
                if isinstance(zname, str):
                    zentry[ATTR_NAME] = zname
                store[STORE_ZONES][self._zone_id] = zentry
            if entities:
                store[STORE_ENTITIES] = entities
            if rotation is not None:
                with suppress(TypeError, ValueError):
                    store[ATTR_ROTATION_DEG] = round(float(rotation))
            if input_units in SUPPORTED_INPUT_UNITS:
                store[ATTR_INPUT_UNITS] = input_units

            # Cache locally for our attributes
            self._coords = {ATTR_SHAPE: shape, ATTR_DATA: data}
            self._entities = entities

            # Let other platform entities know that zones are available
            self.hass.bus.async_fire(
                EVENT_ZONE_UPDATED, {"location": self._location_name}
            )

        # Sync from hass.data for display
        self.update_attributes()

    @callback
    def _handle_zone_update(self, event: Event) -> None:
        """Handle zone data update from the bus."""
        if event.data.get("location") == self._location_name:
            self.update_attributes()
            self.async_write_ha_state()

    def update_attributes(self) -> None:
        """Update the sensor's coordinate attributes from hass.data."""
        integration = self.hass.data.get(DOMAIN, {})
        locations = integration.get(DATA_LOCATIONS, {})
        device_data = locations.get(self._location_name, {})
        if not isinstance(device_data, Mapping):
            self._coords = {ATTR_SHAPE: None, ATTR_DATA: None}
            self._entities = []
            return

        zones = device_data.get(STORE_ZONES, {})
        zone_data = zones.get(self._zone_id) if isinstance(zones, Mapping) else None
        if not isinstance(zone_data, Mapping) or ATTR_SHAPE not in zone_data:
            # Zone undefined or not yet created
            self._coords = {ATTR_SHAPE: None, ATTR_DATA: None}
            self._entities = _normalize_entity_pairs(
                device_data.get(STORE_ENTITIES, [])
            )
            return
        self._coords = {
            ATTR_SHAPE: zone_data.get(ATTR_SHAPE),
            ATTR_DATA: zone_data.get(ATTR_DATA),
        }
        self._entities = _normalize_entity_pairs(device_data.get(STORE_ENTITIES, []))
