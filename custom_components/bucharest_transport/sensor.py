"""Arrival and feed health sensors."""

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .entity import BoardEntity
from .transport.time import TZ


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    for board_id, board in entry.subentries.items():
        if board.subentry_type == "board":
            async_add_entities(
                [ArrivalSensor(coordinator, board_id, "arrival")], config_subentry_id=board_id
            )
    async_add_entities(
        [
            FeedHealthSensor(coordinator, kind)
            for kind in ("vehiclePositions", "tripUpdates", "serviceAlerts", "static")
        ]
    )


class ArrivalSensor(BoardEntity, SensorEntity):
    _attr_translation_key = "next_arrival"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:bus-clock"

    @property
    def native_value(self):
        rows = self.board["arrivals"] if self.board else []
        return datetime.fromisoformat(rows[0]["time"]) if rows else None

    @property
    def extra_state_attributes(self):
        if not self.board:
            return {}
        return {
            key: self.board[key]
            for key in ("status", "realtime_status", "timetable_valid", "arrivals", "generated_at")
        }


class FeedHealthSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:database-clock"

    def __init__(self, coordinator, kind):
        super().__init__(coordinator)
        self.kind = kind
        self._attr_translation_key = {
            "static": "timetable_health",
            "vehiclePositions": "positions_health",
            "tripUpdates": "predictions_health",
            "serviceAlerts": "alerts_health",
        }[kind]
        self._attr_unique_id = f"{coordinator.entry.unique_id}:health:{kind}"

    @property
    def native_value(self):
        diagnostics = self.coordinator.diagnostics()
        if self.kind == "static":
            today = datetime.now(TZ).strftime("%Y%m%d")
            meta = diagnostics["timetable"]
            return (
                "valid"
                if meta.get("start_date", "99999999") <= today <= meta.get("end_date", "")
                else "expired"
            )
        return diagnostics["feeds"][self.kind]["status"]

    @property
    def extra_state_attributes(self):
        diagnostics = self.coordinator.diagnostics()
        return (
            diagnostics["timetable"] if self.kind == "static" else diagnostics["feeds"][self.kind]
        )
