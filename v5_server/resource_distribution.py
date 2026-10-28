"""Public facade for independent platform resource profiles."""
from resource_manifests import (
    ROOT, CONFIG_PATH, PLATFORMS, CHANNELS, get_status, get_platform_config,
    save_platform, preview_profile, generate_profile, remove_profile,
    restore_profile, distribution_entries, get_ios_manifest, resolve_resource,
    register_official_bundle, commit_official_bundles, configure_startup,
)
