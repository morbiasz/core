"""Support for Sensibo wifi-enabled home thermostats."""
from __future__ import annotations

from bisect import bisect_left
from typing import TYPE_CHECKING, Any

from pysensibo.model import SensiboDevice
import voluptuous as vol

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_STATE,
    ATTR_TEMPERATURE,
    PRECISION_TENTHS,
    TEMP_CELSIUS,
    TEMP_FAHRENHEIT,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.temperature import convert as convert_temperature

from .const import DOMAIN
from .coordinator import SensiboDataUpdateCoordinator
from .entity import SensiboDeviceBaseEntity, async_handle_api_call

SERVICE_ASSUME_STATE = "assume_state"
SERVICE_ENABLE_TIMER = "enable_timer"
ATTR_MINUTES = "minutes"
SERVICE_ENABLE_PURE_BOOST = "enable_pure_boost"
SERVICE_DISABLE_PURE_BOOST = "disable_pure_boost"

ATTR_AC_INTEGRATION = "ac_integration"
ATTR_GEO_INTEGRATION = "geo_integration"
ATTR_INDOOR_INTEGRATION = "indoor_integration"
ATTR_OUTDOOR_INTEGRATION = "outdoor_integration"
ATTR_SENSITIVITY = "sensitivity"
BOOST_INCLUSIVE = "boost_inclusive"

PARALLEL_UPDATES = 0

FIELD_TO_FLAG = {
    "fanLevel": ClimateEntityFeature.FAN_MODE,
    "swing": ClimateEntityFeature.SWING_MODE,
    "targetTemperature": ClimateEntityFeature.TARGET_TEMPERATURE,
}

SENSIBO_TO_HA = {
    "cool": HVACMode.COOL,
    "heat": HVACMode.HEAT,
    "fan": HVACMode.FAN_ONLY,
    "auto": HVACMode.HEAT_COOL,
    "dry": HVACMode.DRY,
    "off": HVACMode.OFF,
}

HA_TO_SENSIBO = {value: key for key, value in SENSIBO_TO_HA.items()}

AC_STATE_TO_DATA = {
    "targetTemperature": "target_temp",
    "fanLevel": "fan_mode",
    "on": "device_on",
    "mode": "hvac_mode",
    "swing": "swing_mode",
}


def _find_valid_target_temp(target: int, valid_targets: list[int]) -> int:
    if target <= valid_targets[0]:
        return valid_targets[0]
    if target >= valid_targets[-1]:
        return valid_targets[-1]
    return valid_targets[bisect_left(valid_targets, target)]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Sensibo climate entry."""

    coordinator: SensiboDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        SensiboClimate(coordinator, device_id)
        for device_id, device_data in coordinator.data.parsed.items()
    ]

    async_add_entities(entities)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_ASSUME_STATE,
        {
            vol.Required(ATTR_STATE): vol.In(["on", "off"]),
        },
        "async_assume_state",
    )
    platform.async_register_entity_service(
        SERVICE_ENABLE_TIMER,
        {
            vol.Required(ATTR_MINUTES): cv.positive_int,
        },
        "async_enable_timer",
    )
    platform.async_register_entity_service(
        SERVICE_ENABLE_PURE_BOOST,
        {
            vol.Required(ATTR_AC_INTEGRATION): bool,
            vol.Required(ATTR_GEO_INTEGRATION): bool,
            vol.Required(ATTR_INDOOR_INTEGRATION): bool,
            vol.Required(ATTR_OUTDOOR_INTEGRATION): bool,
            vol.Required(ATTR_SENSITIVITY): vol.In(["Normal", "Sensitive"]),
        },
        "async_enable_pure_boost",
    )


class SensiboClimate(SensiboDeviceBaseEntity, ClimateEntity):
    """Representation of a Sensibo device."""

    def __init__(
        self, coordinator: SensiboDataUpdateCoordinator, device_id: str
    ) -> None:
        """Initiate Sensibo Climate."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = device_id
        self._attr_temperature_unit = (
            TEMP_CELSIUS if self.device_data.temp_unit == "C" else TEMP_FAHRENHEIT
        )
        self._attr_supported_features = self.get_features()
        self._attr_precision = PRECISION_TENTHS

    def get_features(self) -> int:
        """Get supported features."""
        features = 0
        for key in self.device_data.full_features:
            if key in FIELD_TO_FLAG:
                features |= FIELD_TO_FLAG[key]
        return features

    @property
    def current_humidity(self) -> int | None:
        """Return the current humidity."""
        return self.device_data.humidity

    @property
    def hvac_mode(self) -> HVACMode:
        """Return hvac operation."""
        if self.device_data.device_on and self.device_data.hvac_mode:
            return SENSIBO_TO_HA[self.device_data.hvac_mode]
        return HVACMode.OFF

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return the list of available hvac operation modes."""
        hvac_modes = []
        if TYPE_CHECKING:
            assert self.device_data.hvac_modes
        for mode in self.device_data.hvac_modes:
            hvac_modes.append(SENSIBO_TO_HA[mode])
        return hvac_modes if hvac_modes else [HVACMode.OFF]

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        if self.device_data.temp:
            return convert_temperature(
                self.device_data.temp,
                TEMP_CELSIUS,
                self.temperature_unit,
            )
        return None

    @property
    def temperature_unit(self) -> str:
        """Return temperature unit."""
        return TEMP_CELSIUS if self.device_data.temp_unit == "C" else TEMP_FAHRENHEIT

    @property
    def target_temperature(self) -> float | None:
        """Return the temperature we try to reach."""
        target_temp: int | None = self.device_data.target_temp
        return target_temp

    @property
    def target_temperature_step(self) -> float | None:
        """Return the supported step of target temperature."""
        target_temp_step: int = self.device_data.temp_step
        return target_temp_step

    @property
    def fan_mode(self) -> str | None:
        """Return the fan setting."""
        fan_mode: str | None = self.device_data.fan_mode
        return fan_mode

    @property
    def fan_modes(self) -> list[str] | None:
        """Return the list of available fan modes."""
        if self.device_data.fan_modes:
            return self.device_data.fan_modes
        return None

    @property
    def swing_mode(self) -> str | None:
        """Return the swing setting."""
        swing_mode: str | None = self.device_data.swing_mode
        return swing_mode

    @property
    def swing_modes(self) -> list[str] | None:
        """Return the list of available swing modes."""
        if self.device_data.swing_modes:
            return self.device_data.swing_modes
        return None

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        min_temp: int = self.device_data.temp_list[0]
        return min_temp

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        max_temp: int = self.device_data.temp_list[-1]
        return max_temp

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.device_data.available and super().available

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        if "targetTemperature" not in self.device_data.active_features:
            raise HomeAssistantError(
                "Current mode doesn't support setting Target Temperature"
            )

        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            raise ValueError("No target temperature provided")

        if temperature == self.target_temperature:
            return

        new_temp = _find_valid_target_temp(temperature, self.device_data.temp_list)
        await self.async_send_api_call(
            device_data=self.device_data,
            key=AC_STATE_TO_DATA["targetTemperature"],
            value=new_temp,
            name="targetTemperature",
            assumed_state=False,
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new target fan mode."""
        if "fanLevel" not in self.device_data.active_features:
            raise HomeAssistantError("Current mode doesn't support setting Fanlevel")

        await self.async_send_api_call(
            device_data=self.device_data,
            key=AC_STATE_TO_DATA["fanLevel"],
            value=fan_mode,
            name="fanLevel",
            assumed_state=False,
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target operation mode."""
        if hvac_mode == HVACMode.OFF:
            await self.async_send_api_call(
                device_data=self.device_data,
                key=AC_STATE_TO_DATA["on"],
                value=False,
                name="on",
                assumed_state=False,
            )
            return

        # Turn on if not currently on.
        if not self.device_data.device_on:
            await self.async_send_api_call(
                device_data=self.device_data,
                key=AC_STATE_TO_DATA["on"],
                value=True,
                name="on",
                assumed_state=False,
            )

        await self.async_send_api_call(
            device_data=self.device_data,
            key=AC_STATE_TO_DATA["mode"],
            value=HA_TO_SENSIBO[hvac_mode],
            name="mode",
            assumed_state=False,
        )

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set new target swing operation."""
        if "swing" not in self.device_data.active_features:
            raise HomeAssistantError("Current mode doesn't support setting Swing")

        await self.async_send_api_call(
            device_data=self.device_data,
            key=AC_STATE_TO_DATA["swing"],
            value=swing_mode,
            name="swing",
            assumed_state=False,
        )

    async def async_turn_on(self) -> None:
        """Turn Sensibo unit on."""
        await self.async_send_api_call(
            device_data=self.device_data,
            key=AC_STATE_TO_DATA["on"],
            value=True,
            name="on",
            assumed_state=False,
        )

    async def async_turn_off(self) -> None:
        """Turn Sensibo unit on."""
        await self.async_send_api_call(
            device_data=self.device_data,
            key=AC_STATE_TO_DATA["on"],
            value=False,
            name="on",
            assumed_state=False,
        )

    async def async_assume_state(self, state: str) -> None:
        """Sync state with api."""
        await self.async_send_api_call(
            device_data=self.device_data,
            key=AC_STATE_TO_DATA["on"],
            value=state != HVACMode.OFF,
            name="on",
            assumed_state=True,
        )

    async def async_enable_timer(self, minutes: int) -> None:
        """Enable the timer."""
        new_state = bool(self.device_data.ac_states["on"] is False)
        params = {
            "minutesFromNow": minutes,
            "acState": {**self.device_data.ac_states, "on": new_state},
        }
        await self.api_call_custom_service_timer(
            device_data=self.device_data,
            key="timer_on",
            value=True,
            command="set_timer",
            data=params,
        )

    async def async_enable_pure_boost(
        self,
        ac_integration: bool | None = None,
        geo_integration: bool | None = None,
        indoor_integration: bool | None = None,
        outdoor_integration: bool | None = None,
        sensitivity: str | None = None,
    ) -> None:
        """Enable Pure Boost Configuration."""

        params: dict[str, str | bool] = {
            "enabled": True,
        }
        if sensitivity is not None:
            params["sensitivity"] = sensitivity[0]
        if indoor_integration is not None:
            params["measurementsIntegration"] = indoor_integration
        if ac_integration is not None:
            params["acIntegration"] = ac_integration
        if geo_integration is not None:
            params["geoIntegration"] = geo_integration
        if outdoor_integration is not None:
            params["primeIntegration"] = outdoor_integration

        await self.api_call_custom_service_pure_boost(
            device_data=self.device_data,
            key="pure_boost_enabled",
            value=True,
            command="set_pure_boost",
            data=params,
        )

    @async_handle_api_call
    async def async_send_api_call(
        self,
        device_data: SensiboDevice,
        key: Any,
        value: Any,
        name: str,
        assumed_state: bool = False,
    ) -> bool:
        """Make service call to api."""
        result = await self._client.async_set_ac_state_property(
            self._device_id,
            name,
            value,
            self.device_data.ac_states,
            assumed_state,
        )
        return bool(result.get("result", {}).get("status") == "Success")

    @async_handle_api_call
    async def api_call_custom_service_timer(
        self,
        device_data: SensiboDevice,
        key: Any,
        value: Any,
        command: str,
        data: dict,
    ) -> bool:
        """Make service call to api."""
        result = {}
        result = await self._client.async_set_timer(self._device_id, data)
        return bool(result.get("status") == "success")

    @async_handle_api_call
    async def api_call_custom_service_pure_boost(
        self,
        device_data: SensiboDevice,
        key: Any,
        value: Any,
        command: str,
        data: dict,
    ) -> bool:
        """Make service call to api."""
        result = {}
        result = await self._client.async_set_pureboost(self._device_id, data)
        return bool(result.get("status") == "success")
