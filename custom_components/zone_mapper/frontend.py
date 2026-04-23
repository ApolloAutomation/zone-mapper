"""
Optional auto-creation of a Lovelace view for the Zone Mapper card.

The `zone-mapper-card` frontend is installed separately via HACS. This module
only handles seeding a "Zone Mapper" view on the default storage-mode dashboard
the first time a config entry is set up, so users don't have to drop the card
into a view by hand. YAML-mode dashboards are never rewritten.

Lovelace internal APIs (``hass.data["lovelace"]``) are not part of HA's public
contract, so the seeding code is wrapped in a broad try/except: if HA ever
changes the shape of these internals, the integration logs a warning and keeps
working without the auto-view.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .const import (
    AUTO_VIEW_ICON,
    AUTO_VIEW_PATH,
    AUTO_VIEW_PLACEHOLDER_LOCATION,
    AUTO_VIEW_TITLE,
    CARD_TYPE,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def _placeholder_view() -> dict[str, Any]:
    """Return a Lovelace view dict containing a single placeholder card."""
    return {
        "title": AUTO_VIEW_TITLE,
        "path": AUTO_VIEW_PATH,
        "icon": AUTO_VIEW_ICON,
        "cards": [
            {
                "type": CARD_TYPE,
                "location": AUTO_VIEW_PLACEHOLDER_LOCATION,
            }
        ],
    }


def _config_contains_card(config: dict[str, Any]) -> bool:
    for view in config.get("views", []) or []:
        for card in view.get("cards", []) or []:
            if isinstance(card, dict) and card.get("type") == CARD_TYPE:
                return True
    return False


def _resolve_default_dashboard(hass: HomeAssistant) -> object | None:
    """Return the default Lovelace dashboard object, or None if unavailable."""
    lovelace_data = hass.data.get("lovelace")
    if lovelace_data is None:
        _LOGGER.debug("Zone Mapper: lovelace data not available yet.")
        return None
    dashboards = getattr(lovelace_data, "dashboards", None)
    if not isinstance(dashboards, dict):
        _LOGGER.warning(
            "Zone Mapper: unexpected lovelace dashboards shape; skipping"
            " auto-view seeding."
        )
        return None
    # In modern HA the default Overview is registered under the "lovelace" key.
    # The legacy `None` key can hold a stale/phantom LovelaceStorage — prefer
    # "lovelace" so we read and write the dashboard the user actually sees.
    return dashboards.get("lovelace") or dashboards.get(None)


async def async_seed_default_view(hass: HomeAssistant) -> bool:  # noqa: PLR0911
    """
    Append a Zone Mapper view to the default dashboard if one isn't present.

    Returns True when the config entry should be marked as seeded (either the
    view was added, an existing view already contains the card, the dashboard
    is YAML-mode, or an unrecoverable error occurred and we want to stop
    retrying). Returns False only when the environment isn't ready yet and we
    should try again on the next setup.
    """
    try:
        default = _resolve_default_dashboard(hass)
        if default is None:
            return False

        mode = getattr(default, "mode", None)
        if mode == "yaml":
            _LOGGER.info(
                "Zone Mapper: default dashboard is YAML-mode; not rewriting it."
                " Add this card manually to any view:\n"
                "  - type: %s\n    location: %s",
                CARD_TYPE,
                AUTO_VIEW_PLACEHOLDER_LOCATION,
            )
            return True

        async_load = getattr(default, "async_load", None)
        async_save = getattr(default, "async_save", None)
        if async_load is None or async_save is None:
            _LOGGER.warning(
                "Zone Mapper: default dashboard missing load/save hooks;"
                " skipping auto-view seeding."
            )
            return True

        try:
            config = await async_load(force=False)
        except Exception as exc:
            # ConfigNotFound: default dashboard has no stored config yet; HA
            # still serves its auto-generated overview. Seed a config that
            # preserves that behavior via an original-states strategy view,
            # then append the Zone Mapper view alongside it.
            if type(exc).__name__ != "ConfigNotFound":
                raise
            _LOGGER.info(
                "Zone Mapper: default dashboard has no stored config; seeding"
                " one with an auto-generated first view and the Zone Mapper"
                " view."
            )
            config = {
                "views": [
                    {
                        "title": "Home",
                        "strategy": {"type": "original-states"},
                    }
                ]
            }

        if not isinstance(config, dict):
            _LOGGER.debug(
                "Zone Mapper: dashboard config not a dict (%s); skipping seed.",
                type(config).__name__,
            )
            return True

        if _config_contains_card(config):
            _LOGGER.debug("Zone Mapper: default dashboard already contains the card.")
            return True

        views = list(config.get("views") or [])
        views.append(_placeholder_view())
        await async_save({**config, "views": views})
    except Exception:  # noqa: BLE001
        _LOGGER.warning(
            "Zone Mapper: could not seed default Lovelace view; continuing"
            " without auto-view. Add `type: %s` manually to any dashboard.",
            CARD_TYPE,
            exc_info=True,
        )
        return True
    else:
        _LOGGER.info(
            "Zone Mapper: added '%s' view to the default dashboard. Delete it"
            " from the dashboard editor if you prefer to lay out the card"
            " yourself; it won't be re-added.",
            AUTO_VIEW_TITLE,
        )
        return True
