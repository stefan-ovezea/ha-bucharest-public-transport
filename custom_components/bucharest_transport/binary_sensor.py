"""Relevant, fresh service alerts for saved boards."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity

from .entity import BoardEntity


async def async_setup_entry(hass, entry, async_add_entities):
    for board_id, board in entry.subentries.items():
        if board.subentry_type == "board":
            async_add_entities(
                [AlertSensor(entry.runtime_data, board_id, "alerts")], config_subentry_id=board_id
            )


class AlertSensor(BoardEntity, BinarySensorEntity):
    _attr_translation_key = "service_alerts"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def available(self):
        return super().available and self.board["alerts_status"] == "fresh"

    @property
    def is_on(self):
        return bool(self.board and self.board["alerts"])

    @property
    def extra_state_attributes(self):
        return {"alerts": self.board["alerts"][:20]} if self.board else {}
