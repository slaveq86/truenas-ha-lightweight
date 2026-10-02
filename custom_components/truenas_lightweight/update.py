"""TrueNAS software update (read-only: shows availability, never installs)."""

from __future__ import annotations

from homeassistant.components.update import UpdateEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import TrueNASConfigEntry, TrueNASCoordinator
from .entity import TrueNASEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TrueNASConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([TrueNASUpdateEntity(entry.runtime_data)])


class TrueNASUpdateEntity(TrueNASEntity, UpdateEntity):
    """A newer TrueNAS release on the configured train; no supported features, so there's no install button."""

    _attr_translation_key = "system_update"
    _attr_title = "TrueNAS"

    def __init__(self, coordinator: TrueNASCoordinator) -> None:
        super().__init__(coordinator, "system_update")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.update is not None

    @property
    def installed_version(self) -> str | None:
        return self.coordinator.data.system.version

    @property
    def latest_version(self) -> str | None:
        update = self.coordinator.data.update
        if update is not None and update.available:
            return update.version
        return self.installed_version

    @property
    def release_url(self) -> str | None:
        update = self.coordinator.data.update
        return update.release_url if update is not None and update.available else None

    @property
    def release_summary(self) -> str | None:
        update = self.coordinator.data.update
        if update is not None and update.reboot_required:
            return "An update was applied in TrueNAS; reboot to finish installing it."
        return None
