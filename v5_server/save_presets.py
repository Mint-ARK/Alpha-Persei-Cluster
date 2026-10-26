"""Shared presets for the SDK save-creation menu."""
from dataclasses import dataclass
from save_generator import SaveOptions


@dataclass(frozen=True)
class SavePreset:
    key: str
    title: str
    label: str
    options: SaveOptions


# Menu ports are reused only while this client's create mode is active.
PRESET_SLOTS = {
    2: SavePreset('fresh', '完整开荒', '🌱【完整开荒】1级·保留引导', SaveOptions()),
    3: SavePreset('skip_prologue', '跳过序章', '⏩【跳过序章】1级·序章已通关',
                  SaveOptions(skip_intro=True, clear_through=0)),
    4: SavePreset('quick_start', '快速体验', '🚀【快速体验】70级·前五章全通',
                  SaveOptions(level=70, skip_intro=True, skip_feature_guides=True,
                              clear_through=5, noob_tasks_ready=True)),
}
