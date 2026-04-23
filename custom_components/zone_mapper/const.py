"""Constants for the Zone Mapper integration."""

# Core domain
DOMAIN = "zone_mapper"

# Services
SERVICE_UPDATE_ZONE = "update_zone"

# Event names
EVENT_ZONE_UPDATED = f"{DOMAIN}_zone_updated"

# hass.data keys
DATA_LOCATIONS = "locations"
DATA_PLATFORMS_LOADED = "platforms_loaded"

# Location store keys
STORE_ZONES = "zones"
STORE_ENTITIES = "entities"

# Attribute / data keys. Persisted coordinates are rounded to whole mm
ATTR_SHAPE = "shape"
ATTR_DATA = "data"
ATTR_POINTS = "points"
ATTR_X_MIN = "x_min"
ATTR_X_MAX = "x_max"
ATTR_Y_MIN = "y_min"
ATTR_Y_MAX = "y_max"
ATTR_CX = "cx"
ATTR_CY = "cy"
ATTR_RX = "rx"
ATTR_RY = "ry"
ATTR_ROTATION_DEG = "rotation_deg"
ATTR_NAME = "name"

# Shapes
SHAPE_RECT = "rect"
SHAPE_ELLIPSE = "ellipse"
SHAPE_POLYGON = "polygon"
SHAPE_NONE = "none"
SUPPORTED_SHAPES = (SHAPE_NONE, SHAPE_RECT, SHAPE_ELLIPSE, SHAPE_POLYGON)

# Limits / defaults
POLYGON_MAX_POINTS = 32
POLYGON_MIN_POINTS = 3

# Sensor / entity naming fragments
COORD_SENSOR_UNIQUE_ID_FMT = "zone_mapper_{location}_zone_{zone_id}"
PRESENCE_SENSOR_UNIQUE_ID_FMT = "zone_mapper_{location}_zone_{zone_id}_presence"

# Options flow / auto-view seeding
CONF_AUTO_CREATE_VIEW = "auto_create_view"
DEFAULT_AUTO_CREATE_VIEW = True
SEEDED_DEFAULT_VIEW_FLAG = "seeded_default_view"
AUTO_VIEW_TITLE = "Zone Mapper"
AUTO_VIEW_PATH = "zone-mapper"
AUTO_VIEW_ICON = "mdi:map-marker-radius"
AUTO_VIEW_PLACEHOLDER_LOCATION = "Home"
CARD_TYPE = "custom:zone-mapper-card"

# Log / warning templates
WARN_POLY_INSUFFICIENT = (
    "Polygon zone %s in location '%s' has insufficient points (<3); clearing zone."
)
WARN_POLY_TRUNCATING = (
    "Polygon zone %s in location '%s' has %d points; truncating to %d."
)
WARN_RECT_INVALID = (
    "Rectangle zone %s in location '%s' has invalid bounds; clearing zone."
)
WARN_RECT_NON_NUM = (
    "Rectangle zone %s in location '%s' has non-numeric bounds; clearing zone."
)
WARN_ELLIPSE_NON_POSITIVE = (
    "Ellipse zone %s in location '%s' has non-positive radii; clearing zone."
)
WARN_ELLIPSE_INVALID = (
    "Ellipse zone %s in location '%s' has invalid radii; clearing zone."
)
