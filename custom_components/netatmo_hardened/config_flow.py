"""Config flow for Netatmo."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any, override

import probatio
from homeassistant.config_entries import ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_SHOW_ON_MAP, CONF_UUID
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers import config_validation as cv

from .api import get_api_scopes
from .const import (
    CONF_AREA_NAME,
    CONF_ENABLE_WEBHOOK,
    CONF_LAT_NE,
    CONF_LAT_SW,
    CONF_LON_NE,
    CONF_LON_SW,
    CONF_NEW_AREA,
    CONF_PUBLIC_MODE,
    CONF_WEATHER_AREAS,
    DEFAULT_ENABLE_WEBHOOK,
    DOMAIN,
)
from .coordinator import NetatmoConfigEntry
from .helper import normalise_coordinate

_LOGGER = logging.getLogger(__name__)

# Legal magnitude of each coordinate, per WGS 84.
LATITUDE_LIMIT = 90.0
LONGITUDE_LIMIT = 180.0


class NetatmoFlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Config flow to handle Netatmo OAuth2 authentication."""

    DOMAIN = DOMAIN

    @staticmethod
    @callback
    @override
    def async_get_options_flow(
        config_entry: NetatmoConfigEntry,
    ) -> OptionsFlow:
        """Get the options flow for this handler."""
        return NetatmoOptionsFlowHandler(config_entry)

    @property
    @override
    def logger(self) -> logging.Logger:
        """Return logger."""
        return logging.getLogger(__name__)

    @property
    @override
    def extra_authorize_data(self) -> dict:
        """Extra data that needs to be appended to the authorize url."""
        scopes = get_api_scopes(self.flow_impl.domain)
        return {"scope": " ".join(scopes)}

    @override
    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Handle a flow start."""
        await self.async_set_unique_id(DOMAIN)
        return await super().async_step_user(user_input)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Perform reauth upon an API authentication error."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Dialog that informs the user that reauth is required."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")

        return await self.async_step_user()

    @override
    async def async_oauth_create_entry(self, data: dict) -> ConfigFlowResult:
        """Create an oauth config entry or update existing entry for reauth."""
        existing_entry = await self.async_set_unique_id(DOMAIN)
        if existing_entry:
            # [hardened-fork] Deliberately does NOT reload here. From 2026.12
            # Home Assistant treats "a config entry update listener combined
            # with a reloading method in the config flow" as an error, and this
            # integration needs the update listener so that a public weather
            # area change does not cost a full reload. The reload decision now
            # lives in async_config_entry_updated, which can tell a credential
            # change from an options change. See docs/COMPATIBILITY.md, D-1.
            self.hass.config_entries.async_update_entry(existing_entry, data=data)
            return self.async_abort(reason="reauth_successful")

        return await super().async_oauth_create_entry(data)


class NetatmoOptionsFlowHandler(OptionsFlow):
    """Handle Netatmo options."""

    def __init__(self, config_entry: NetatmoConfigEntry) -> None:
        """Initialize Netatmo options flow."""
        self.options = dict(config_entry.options)
        self.options.setdefault(CONF_WEATHER_AREAS, {})

    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Choose what to configure."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["push_events", "public_weather_areas"],
        )

    async def async_step_push_events(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Enable or disable webhook-delivered push events.

        [hardened-fork] Added in 0.1.2, default off.

        Netatmo will only register a webhook against a publicly reachable HTTPS
        endpoint on port 443. An installation without one - which is the common
        case for a purely local Home Assistant - cannot use push events at all:
        every registration is refused with `400 - invalid webhook url (WH006)`.
        Leaving the subsystem switched on in that situation produces permanent,
        futile API traffic against a rate-limited account and a warning in the
        log every fifteen minutes.

        It is therefore opt-in rather than opt-out: the operator is the only
        party who knows whether their deployment is reachable from the
        internet, and the safe default is not to assume that it is. Turning
        this on without such an endpoint is harmless but useless - it will
        raise a repair issue explaining why (defect E-010).
        """
        if user_input is not None:
            self.options[CONF_ENABLE_WEBHOOK] = user_input[CONF_ENABLE_WEBHOOK]
            return self._create_options_entry()

        return self.async_show_form(
            step_id="push_events",
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_ENABLE_WEBHOOK,
                        default=self.options.get(
                            CONF_ENABLE_WEBHOOK, DEFAULT_ENABLE_WEBHOOK
                        ),
                    ): bool,
                }
            ),
        )

    async def async_step_public_weather_areas(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Manage configuration of Netatmo public weather areas."""
        errors: dict = {}

        if user_input is not None:
            new_client = user_input.pop(CONF_NEW_AREA, None)
            areas = user_input.pop(CONF_WEATHER_AREAS, [])
            user_input[CONF_WEATHER_AREAS] = {
                area: self.options[CONF_WEATHER_AREAS][area] for area in areas
            }
            self.options.update(user_input)
            if new_client:
                return await self.async_step_public_weather(
                    user_input={CONF_NEW_AREA: new_client}
                )

            return self._create_options_entry()

        weather_areas = list(self.options[CONF_WEATHER_AREAS])

        data_schema = probatio.Schema(
            {
                probatio.Optional(
                    CONF_WEATHER_AREAS,
                    default=weather_areas,
                ): cv.multi_select(dict.fromkeys(weather_areas)),
                probatio.Optional(CONF_NEW_AREA): str,
            }
        )
        return self.async_show_form(
            step_id="public_weather_areas",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_public_weather(self, user_input: dict) -> ConfigFlowResult:
        """Manage configuration of Netatmo public weather sensors."""
        if user_input is not None and CONF_NEW_AREA not in user_input:
            self.options[CONF_WEATHER_AREAS][user_input[CONF_AREA_NAME]] = (
                fix_coordinates(user_input)
            )

            self.options[CONF_WEATHER_AREAS][user_input[CONF_AREA_NAME]][CONF_UUID] = (
                str(uuid.uuid4())
            )

            return await self.async_step_public_weather_areas()

        orig_options = self.config_entry.options.get(CONF_WEATHER_AREAS, {}).get(
            user_input[CONF_NEW_AREA], {}
        )

        default_longitude = self.hass.config.longitude
        default_latitude = self.hass.config.latitude
        default_size = 0.04

        data_schema = probatio.Schema(
            {
                probatio.Optional(
                    CONF_AREA_NAME, default=user_input[CONF_NEW_AREA]
                ): str,
                probatio.Optional(
                    CONF_LAT_NE,
                    default=orig_options.get(
                        CONF_LAT_NE, default_latitude + default_size
                    ),
                ): cv.latitude,
                probatio.Optional(
                    CONF_LON_NE,
                    default=orig_options.get(
                        CONF_LON_NE, default_longitude + default_size
                    ),
                ): cv.longitude,
                probatio.Optional(
                    CONF_LAT_SW,
                    default=orig_options.get(
                        CONF_LAT_SW, default_latitude - default_size
                    ),
                ): cv.latitude,
                probatio.Optional(
                    CONF_LON_SW,
                    default=orig_options.get(
                        CONF_LON_SW, default_longitude - default_size
                    ),
                ): cv.longitude,
                probatio.Required(
                    CONF_PUBLIC_MODE,
                    default=orig_options.get(CONF_PUBLIC_MODE, "avg"),
                ): probatio.In(["avg", "max", "min"]),
                probatio.Required(
                    CONF_SHOW_ON_MAP,
                    default=orig_options.get(CONF_SHOW_ON_MAP, False),
                ): bool,
            }
        )

        return self.async_show_form(step_id="public_weather", data_schema=data_schema)

    def _create_options_entry(self) -> ConfigFlowResult:
        """Write the accumulated options back to the config entry.

        [hardened-fork] The title was "Netatmo Public Weather", which stopped
        being true in 0.1.2 when push events joined the options flow. Home
        Assistant ignores the title of an options entry, so this was only ever
        a label for whoever read the code next - which is reason to keep it
        accurate, not reason to leave it wrong.
        """
        return self.async_create_entry(title="Netatmo options", data=self.options)


def fix_coordinates(user_input: dict) -> dict:
    """Fix coordinates if they don't comply with the Netatmo API.

    [hardened-fork] Precision is checked numerically. Upstream inspected
    ``str(value).split(".")[1]``, which raises ``IndexError`` whenever Python
    renders the float in scientific notation - true of any magnitude below
    1e-4, so any location within roughly 11 metres of the equator or the prime
    meridian crashed the options flow (defect C-21).
    """
    # [hardened-fork] Each coordinate is normalised against its own legal
    # magnitude, so an exact boundary value is nudged inward rather than out of
    # range - 90.0 used to become 90.0000001 (defect E-006).
    for coordinate, limit in (
        (CONF_LAT_NE, LATITUDE_LIMIT),
        (CONF_LAT_SW, LATITUDE_LIMIT),
        (CONF_LON_NE, LONGITUDE_LIMIT),
        (CONF_LON_SW, LONGITUDE_LIMIT),
    ):
        user_input[coordinate] = normalise_coordinate(
            float(user_input[coordinate]), limit
        )

    # Swap coordinates if entered in wrong order
    if user_input[CONF_LAT_NE] < user_input[CONF_LAT_SW]:
        user_input[CONF_LAT_NE], user_input[CONF_LAT_SW] = (
            user_input[CONF_LAT_SW],
            user_input[CONF_LAT_NE],
        )
    if user_input[CONF_LON_NE] < user_input[CONF_LON_SW]:
        user_input[CONF_LON_NE], user_input[CONF_LON_SW] = (
            user_input[CONF_LON_SW],
            user_input[CONF_LON_NE],
        )

    return user_input
