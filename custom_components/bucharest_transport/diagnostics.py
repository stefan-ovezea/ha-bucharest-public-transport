"""Integration diagnostics contain public feed metadata, not user configuration."""


async def async_get_config_entry_diagnostics(hass, entry):
    return entry.runtime_data.diagnostics()
