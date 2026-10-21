"""Common board entities."""

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME


class BoardEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, board_id, suffix):
        super().__init__(coordinator)
        self.board_id = board_id
        self._attr_unique_id = f"{coordinator.entry.unique_id}:board:{board_id}:{suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, board_id)},
            name=coordinator.entry.subentries[board_id].title,
            manufacturer="TPBI",
            model=NAME,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def board(self):
        return (self.coordinator.data or {}).get("boards", {}).get(self.board_id)

    @property
    def available(self):
        return self.board is not None
