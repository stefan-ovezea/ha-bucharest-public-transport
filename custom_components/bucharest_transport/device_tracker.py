"""GPS trackers for vehicles on explicitly watched lines."""

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import CoordinatorEntity


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    known = set()
    prefix = f"{entry.unique_id}:vehicle:"

    @callback
    def update():
        vehicles = {v["key"] for v in _watched(coordinator)}
        for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
            if entity.unique_id.startswith(prefix):
                vehicles.add(entity.unique_id.removeprefix(prefix))
        new = vehicles - known
        known.update(new)
        async_add_entities([VehicleTracker(coordinator, key) for key in sorted(new)])

    entry.async_on_unload(coordinator.async_add_listener(update))
    update()


def _watched(coordinator):
    routes = coordinator.entry.options.get("tracked_route_ids", [])
    if not routes:
        return []
    direction = coordinator.entry.options.get("tracked_direction", "both")
    return [
        v
        for v in coordinator.vehicles(routes)
        if direction == "both" or v["direction_id"] == int(direction)
    ]


class VehicleTracker(CoordinatorEntity, TrackerEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:bus"

    def __init__(self, coordinator, key):
        super().__init__(coordinator)
        self.key = key
        self._attr_unique_id = f"{coordinator.entry.unique_id}:vehicle:{key}"
        self._attr_name = f"Vehicle {key}"

    @property
    def vehicle(self):
        return self.coordinator.vehicle(self.key)

    @property
    def available(self):
        return self.vehicle is not None

    @property
    def source_type(self):
        return SourceType.GPS

    @property
    def latitude(self):
        return self.vehicle["latitude"] if self.vehicle else None

    @property
    def longitude(self):
        return self.vehicle["longitude"] if self.vehicle else None

    @property
    def extra_state_attributes(self):
        return self.vehicle or {}
