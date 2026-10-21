"""UI setup, watched lines, and station board subentries."""

import probatio as vol
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigSubentryFlow,
    OptionsFlow,
)
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode

from .const import DOMAIN, NAME


def _select(options, multiple=False):
    return SelectSelector(
        SelectSelectorConfig(options=options, multiple=multiple, mode=SelectSelectorMode.DROPDOWN)
    )


def route_options(coordinator):
    return [
        {"value": r["route_id"], "label": f"{r['short_name']} · {r['agency']} · {r['long_name']}"}
        for r in sorted(
            coordinator.engine.schedule.routes.values(),
            key=lambda r: (r["agency"], r["short_name"]),
        )
    ]


class BucharestTransportFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id("tpbi_regional")
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title=NAME, data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    def async_get_options_flow(config_entry):
        return TransportOptionsFlow()

    @classmethod
    def async_get_supported_subentry_types(cls, config_entry):
        return {"board": BoardSubentryFlow}


class TransportOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        coordinator = self.config_entry.runtime_data
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "tracked_route_ids",
                        default=self.config_entry.options.get("tracked_route_ids", []),
                    ): _select(route_options(coordinator), True),
                    vol.Optional(
                        "tracked_direction",
                        default=self.config_entry.options.get("tracked_direction", "both"),
                    ): _select(
                        [
                            {"value": "both", "label": "Both directions / Ambele sensuri"},
                            {"value": "0", "label": "0"},
                            {"value": "1", "label": "1"},
                        ]
                    ),
                }
            ),
        )


class BoardSubentryFlow(ConfigSubentryFlow):
    def __init__(self):
        self._stop_id = None
        self._results = []

    @property
    def coordinator(self):
        return self._get_entry().runtime_data

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            self._results = await self.coordinator.async_search(user_input["query"])
            if self._results:
                return await self.async_step_stop()
            errors["base"] = "no_stops"
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema({vol.Required("query"): str}), errors=errors
        )

    async def async_step_stop(self, user_input=None):
        if user_input is not None:
            self._stop_id = user_input["stop_id"]
            return await self.async_step_filters()
        options = [
            {
                "value": s["stop_id"],
                "label": f"{s['name']} · {s['platform'] or s['stop_id']} · "
                + ", ".join(sorted({r["short_name"] + " " + r["agency"] for r in s["routes"]})),
            }
            for s in self._results
        ]
        return self.async_show_form(
            step_id="stop", data_schema=vol.Schema({vol.Required("stop_id"): _select(options)})
        )

    async def async_step_filters(self, user_input=None):
        if user_input is not None:
            data = {
                "stop_id": self._stop_id,
                "route_ids": user_input.get("route_ids", []),
                "estimate_missing_times": user_input.get("estimate_missing_times", False),
            }
            if user_input["direction"] != "both":
                data["direction_id"] = int(user_input["direction"])
            if self.source == SOURCE_RECONFIGURE:
                return self.async_update_reload_and_abort(
                    self._get_entry(),
                    self._get_reconfigure_subentry(),
                    title=user_input["name"],
                    data=data,
                )
            return self.async_create_entry(title=user_input["name"], data=data)
        stop = self.coordinator.engine.schedule.stops[self._stop_id]
        allowed = {r["route_id"] for r in stop["routes"]}
        saved = self._get_reconfigure_subentry() if self.source == SOURCE_RECONFIGURE else None
        defaults = dict(saved.data) if saved else {}
        return self.async_show_form(
            step_id="filters",
            data_schema=vol.Schema(
                {
                    vol.Required("name", default=saved.title if saved else stop["name"]): str,
                    vol.Optional("route_ids", default=defaults.get("route_ids", [])): _select(
                        [r for r in route_options(self.coordinator) if r["value"] in allowed], True
                    ),
                    vol.Required(
                        "direction", default=str(defaults.get("direction_id", "both"))
                    ): _select(
                        [
                            {"value": "both", "label": "Both directions / Ambele sensuri"},
                            {"value": "0", "label": "0"},
                            {"value": "1", "label": "1"},
                        ]
                    ),
                    vol.Optional(
                        "estimate_missing_times",
                        default=defaults.get("estimate_missing_times", False),
                    ): bool,
                }
            ),
        )

    async def async_step_reconfigure(self, user_input=None):
        return await self.async_step_user(user_input)
