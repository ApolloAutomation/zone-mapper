"""Binary sensor platform for Zone Mapper."""

import logging
import math
from collections.abc import Callable, Iterator, Mapping
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import slugify

from .const import (
    ATTR_CX,
    ATTR_CY,
    ATTR_DATA,
    ATTR_INPUT_UNITS,
    ATTR_POINTS,
    ATTR_ROTATION_DEG,
    ATTR_RX,
    ATTR_RY,
    ATTR_SHAPE,
    ATTR_X_MAX,
    ATTR_X_MIN,
    ATTR_Y_MAX,
    ATTR_Y_MIN,
    DATA_LOCATIONS,
    DOMAIN,
    EVENT_ZONE_UPDATED,
    POLYGON_MIN_POINTS,
    PRESENCE_SENSOR_UNIQUE_ID_FMT,
    SHAPE_ELLIPSE,
    SHAPE_POLYGON,
    SHAPE_RECT,
    STORE_ENTITIES,
    STORE_ZONES,
)

_LOGGER = logging.getLogger(__name__)

_LINE_INTERSECTION_EPSILON = 1e-12
_UNIT_ALIASES = {
    "millimeter": "mm",
    "millimeters": "mm",
    "millimetre": "mm",
    "millimetres": "mm",
    "centimeter": "cm",
    "centimeters": "cm",
    "centimetre": "cm",
    "centimetres": "cm",
    "meter": "m",
    "meters": "m",
    "metre": "m",
    "metres": "m",
    "inch": "in",
    "inches": "in",
    "foot": "ft",
    "feet": "ft",
}
_UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "ft": 304.8}

ShapeData = Mapping[str, Any] | None
ShapeTester = Callable[[float, float, ShapeData], bool]


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_unit(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    unit = value.strip().lower()
    unit = _UNIT_ALIASES.get(unit, unit)
    return unit if unit in _UNIT_TO_MM else None


def _convert_to_mm(value: Any, unit: Any) -> float | None:
    numeric = _coerce_float(value)
    normalized_unit = _normalize_unit(unit)
    if numeric is None or normalized_unit is None:
        return None
    return numeric * _UNIT_TO_MM[normalized_unit]


def _state_unit(state: State, fallback_unit: Any) -> str:
    state_unit = _normalize_unit(state.attributes.get("unit_of_measurement"))
    return state_unit or _normalize_unit(fallback_unit) or "mm"


def _slugify_location(location: str) -> str:
    return slugify(str(location))


def _build_point_rotator(
    rotation_raw: Any,
) -> Callable[[float, float], tuple[float, float]]:
    rotation_val = _coerce_float(rotation_raw)
    if rotation_val is None:
        cos_theta = 1.0
        sin_theta = 0.0
    else:
        rotation_rad = math.radians(rotation_val)
        cos_theta = math.cos(rotation_rad)
        sin_theta = math.sin(rotation_rad)

    def rotate(x_val: float, y_val: float) -> tuple[float, float]:
        return (
            x_val * cos_theta + y_val * sin_theta,
            -x_val * sin_theta + y_val * cos_theta,
        )

    return rotate


def _point_in_rect(x_val: float, y_val: float, rect: ShapeData) -> bool:
    if not isinstance(rect, Mapping):
        return False
    x_min = _coerce_float(rect.get(ATTR_X_MIN))
    x_max = _coerce_float(rect.get(ATTR_X_MAX))
    y_min = _coerce_float(rect.get(ATTR_Y_MIN))
    y_max = _coerce_float(rect.get(ATTR_Y_MAX))
    if x_min is None or x_max is None or y_min is None or y_max is None:
        return False
    if not (x_min < x_max and y_min < y_max):
        return False
    return x_min <= x_val <= x_max and y_min <= y_val <= y_max


def _point_in_ellipse(x_val: float, y_val: float, ellipse: ShapeData) -> bool:
    if not isinstance(ellipse, Mapping):
        return False
    cx = _coerce_float(ellipse.get(ATTR_CX))
    cy = _coerce_float(ellipse.get(ATTR_CY))
    rx = _coerce_float(ellipse.get(ATTR_RX))
    ry = _coerce_float(ellipse.get(ATTR_RY))
    if cx is None or cy is None or rx is None or ry is None:
        return False
    if rx <= 0 or ry <= 0:
        return False
    dx = x_val - cx
    dy = y_val - cy
    return (dx * dx) / (rx * rx) + (dy * dy) / (ry * ry) <= 1.0


def _point_in_polygon(x_val: float, y_val: float, polygon: ShapeData) -> bool:
    if not isinstance(polygon, Mapping):
        return False
    points = polygon.get(ATTR_POINTS)
    if not isinstance(points, list) or len(points) < POLYGON_MIN_POINTS:
        return False
    inside = False
    point_count = len(points)
    for idx in range(point_count):
        point_a = points[idx]
        point_b = points[(idx + 1) % point_count]
        if not isinstance(point_a, Mapping) or not isinstance(point_b, Mapping):
            return False
        x1 = _coerce_float(point_a.get("x"))
        y1 = _coerce_float(point_a.get("y"))
        x2 = _coerce_float(point_b.get("x"))
        y2 = _coerce_float(point_b.get("y"))
        if x1 is None or y1 is None or x2 is None or y2 is None:
            return False
        intersects = (y1 > y_val) != (y2 > y_val)
        if intersects:
            denominator = (y2 - y1) or _LINE_INTERSECTION_EPSILON
            x_at_y = x1 + (x2 - x1) * (y_val - y1) / denominator
            if x_at_y >= x_val:
                inside = not inside
    return inside


_SHAPE_TESTERS: dict[str, ShapeTester] = {
    SHAPE_RECT: _point_in_rect,
    SHAPE_ELLIPSE: _point_in_ellipse,
    SHAPE_POLYGON: _point_in_polygon,
}


async def async_setup_platform(
    hass: HomeAssistant,
    _config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the Zone Mapper binary sensors based on discovery info."""
    if discovery_info is None:
        return

    location_name = discovery_info.get("location")
    if location_name is None:
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
        current_zone_ids: set[int] = set()
        new_entities: list[ZonePresenceBinarySensor] = []

        for raw_zone_id in zones_map:
            zone_id = _coerce_zone_id(raw_zone_id)
            if zone_id is None:
                continue
            current_zone_ids.add(zone_id)
            if zone_id not in added_zone_ids:
                new_entities.append(
                    ZonePresenceBinarySensor(hass, location_name, zone_id)
                )
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


class ZonePresenceBinarySensor(BinarySensorEntity):
    """Representation of a Zone Presence binary sensor."""

    def __init__(self, hass: HomeAssistant, location_name: str, zone_id: int) -> None:
        """Initialize the sensor."""
        self.hass = hass
        self._location_name = location_name
        self._zone_id = zone_id
        self._is_on = False
        self._tracked_entities: list[dict[str, str]] = []
        self._remove_bus_listener: Callable[[], None] | None = None
        self._unsub_state_listener: Callable[[], None] | None = None

        self._attr_device_class = BinarySensorDeviceClass.OCCUPANCY
        self._attr_name = f"{location_name} Zone {zone_id} Presence"
        safe_location = _slugify_location(location_name)
        self._attr_unique_id = PRESENCE_SENSOR_UNIQUE_ID_FMT.format(
            location=safe_location, zone_id=zone_id
        )
        self._attr_should_poll = False

    @property
    def icon(self) -> str:
        """Return the Material Design icon representing motion."""
        return "mdi:motion-sensor"

    @property
    def is_on(self) -> bool:
        """Return whether the binary sensor detects presence."""
        return self._is_on

    async def async_added_to_hass(self) -> None:
        """Register listeners once the entity is added to Home Assistant."""
        await super().async_added_to_hass()

        @callback
        def handle_zone_update(event: Event) -> None:
            if event.data.get("location") == self._location_name:
                self.update_tracked_entities()
                self.async_schedule_update_ha_state(force_refresh=True)

        self._remove_bus_listener = self.hass.bus.async_listen(
            EVENT_ZONE_UPDATED, handle_zone_update
        )
        self.update_tracked_entities()

    async def async_will_remove_from_hass(self) -> None:
        """Tear down listeners before removing the entity."""
        if self._remove_bus_listener:
            self._remove_bus_listener()
            self._remove_bus_listener = None
        if self._unsub_state_listener:
            self._unsub_state_listener()
            self._unsub_state_listener = None

    def update_tracked_entities(self) -> None:
        """Refresh the tracked XY entity pairs for this zone."""
        if self._unsub_state_listener:
            self._unsub_state_listener()
            self._unsub_state_listener = None

        integration = self.hass.data.get(DOMAIN, {})
        locations = integration.get(DATA_LOCATIONS, {})
        device_data = locations.get(self._location_name, {})
        if not isinstance(device_data, Mapping):
            self._tracked_entities = []
            return

        raw_entities = device_data.get(STORE_ENTITIES, [])
        self._tracked_entities = raw_entities if isinstance(raw_entities, list) else []

        entity_ids = [
            entity_id
            for entity_id in self._flatten_tracked_entity_ids()
            if entity_id is not None
        ]

        if entity_ids:
            self._unsub_state_listener = async_track_state_change_event(
                self.hass,
                entity_ids,
                self.handle_entity_update,
            )

    @callback
    def handle_entity_update(self, _event: Event[EventStateChangedData]) -> None:
        """Handle state change of a tracked entity."""
        self.async_schedule_update_ha_state(force_refresh=True)

    async def async_update(self) -> None:
        """Fetch new state data for the sensor."""
        zone_def, rotation_raw, input_units = self._resolve_zone_definition()

        if not self._tracked_entities or zone_def is None:
            self._is_on = False
            return

        shape = zone_def.get(ATTR_SHAPE)
        if not isinstance(shape, str):
            self._is_on = False
            return
        data = zone_def.get(ATTR_DATA)
        shape_tester = _SHAPE_TESTERS.get(shape)
        if shape_tester is None:
            self._is_on = False
            return

        rotate_point = _build_point_rotator(rotation_raw)

        for x_val, y_val in self._iter_rotated_coordinates(rotate_point, input_units):
            if shape_tester(x_val, y_val, data):
                self._is_on = True
                return

        self._is_on = False

    def _flatten_tracked_entity_ids(self) -> Iterator[str | None]:
        for pair in self._tracked_entities:
            yield pair.get("x")
            yield pair.get("y")

    def _iter_tracked_entity_pairs(self) -> Iterator[tuple[str, str]]:
        for pair in self._tracked_entities:
            x_id = pair.get("x")
            y_id = pair.get("y")
            if isinstance(x_id, str) and isinstance(y_id, str):
                yield x_id, y_id

    def _iter_rotated_coordinates(
        self,
        rotate: Callable[[float, float], tuple[float, float]],
        input_units: Any,
    ) -> Iterator[tuple[float, float]]:
        for x_id, y_id in self._iter_tracked_entity_pairs():
            coords = self._get_coordinate_pair(x_id, y_id, input_units)
            if coords is None:
                continue
            yield rotate(*coords)

    def _get_coordinate_pair(
        self, x_entity_id: str, y_entity_id: str, input_units: Any
    ) -> tuple[float, float] | None:
        x_state = self.hass.states.get(x_entity_id)
        y_state = self.hass.states.get(y_entity_id)
        if not self._states_are_valid(x_state, y_state):
            return None
        if x_state is None or y_state is None:
            return None
        x_unit = _state_unit(x_state, input_units)
        y_unit = _state_unit(y_state, input_units)
        x_val = _convert_to_mm(x_state.state, x_unit)
        y_val = _convert_to_mm(y_state.state, y_unit)
        if x_val is None or y_val is None:
            return None
        # Ignore origin (0,0) so default or uninitialized readings are skipped.
        if x_val == 0.0 and y_val == 0.0:
            return None
        return x_val, y_val

    @staticmethod
    def _states_are_valid(x_state: State | None, y_state: State | None) -> bool:
        return (
            x_state is not None
            and y_state is not None
            and x_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
            and y_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
        )

    def _resolve_zone_definition(
        self,
    ) -> tuple[Mapping[str, Any] | None, Any, Any]:
        integration = self.hass.data.get(DOMAIN, {})
        locations = integration.get(DATA_LOCATIONS, {})
        device_store = locations.get(self._location_name, {})
        if not isinstance(device_store, Mapping):
            return None, None, None
        zone_store = device_store.get(STORE_ZONES, {})
        if isinstance(zone_store, Mapping):
            zone_def = zone_store.get(self._zone_id)
        else:
            zone_def = None
        resolved_zone = zone_def if isinstance(zone_def, Mapping) else None
        return (
            resolved_zone,
            device_store.get(ATTR_ROTATION_DEG),
            device_store.get(ATTR_INPUT_UNITS),
        )
