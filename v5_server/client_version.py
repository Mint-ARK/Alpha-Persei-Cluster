"""Bind shared gameplay services to a connection's actual resource baseline."""
import res_version_manager as versions

# GameToSDK: 0 Android, 1 iOS (IDFA/iOS-currency branch), 4 PC payment branch.
PLATFORM_TYPES = {'0': 'android', '1': 'ios', '4': 'pc'}
CHANNELS = {'7': 'ios', '8': 'android', '9': 'pc'}


def platform_from_login(data):
    if not isinstance(data, dict):
        return None
    kind = str(data.get('platform_type', '')).lower()
    if kind in ('pc', 'android', 'ios'):
        return kind
    return PLATFORM_TYPES.get(kind) or CHANNELS.get(str(data.get('channel_id', '')))


def resolve(data):
    data = data if isinstance(data, dict) else {}
    platform = platform_from_login(data)
    reported = str(data.get('resource_vs') or '').strip()
    if platform is None:
        # Old callers without a platform retain the configured fallback, but a
        # known real resource build is still stronger than the process default.
        version = reported if reported in versions.VERSION_CONFIGS else versions.get_default_version()
        return dict(platform=None, version=version, reported=reported, advertised='', reason='legacy_login')
    import resource_manifests
    return resource_manifests.resolve_connection_version(platform, reported)


def http_platform(request):
    body = request.get_json(silent=True) if request.is_json else {}
    body = body if isinstance(body, dict) else {}
    named = str(request.args.get('platformType') or request.form.get('platformType') or body.get('platformType') or '').lower()
    if named in ('pc', 'android', 'ios'):
        return named
    agent = request.headers.get('User-Agent', '').lower()
    if any(value in agent for value in ('iphone', 'ipad', 'darwin', 'ios')):
        return 'ios'
    if 'android' in agent:
        return 'android'
    if 'windows' in agent:
        return 'pc'
    return CHANNELS.get(str(request.args.get('channelId') or request.form.get('channelId') or body.get('channelId') or ''), 'pc')
