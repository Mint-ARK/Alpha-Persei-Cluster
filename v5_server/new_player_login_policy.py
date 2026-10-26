"""Experimental login omission, scoped to the owner of a new_player_v1 save."""
import json
from pathlib import Path

from new_player_service import enabled


# These packets establish the login barrier and the basic account caches.
PROTECTED = frozenset((10201, 10043, 10503, 10600, 12001, 12009, 12011,
                       12111, 13009, 14009, 15009, 17009, 24009, 24029,
                       24061, 28001, 32009, 46011, 47001, 47003, 59011,
                       11001, 20007, 20009, 52001, 19029))
CONFIG_PATH = Path(__file__).with_name('new_player_login_policy.json')


class LoginPolicy:
    def __init__(self, db, uid, *, settings=None):
        self.omitted = {}
        self.empty_omissions = {}
        self.mode = 'legacy'
        if not enabled(db, uid):
            return
        # Read once per login, so an A/B switch needs no save reset or restart.
        data = settings if settings is not None else json.loads(CONFIG_PATH.read_text('utf-8'))
        self.mode = data['mode']
        if self.mode not in ('unlocked', 'full_initial'):
            raise ValueError('new_player_login_policy.mode must be unlocked or full_initial')
        groups = data['groups']
        included = set(data.get('include_groups', []))
        if included - groups.keys():
            raise ValueError('Unknown login include_groups: '+str(sorted(included-groups.keys())))
        assigned = set()
        for name, group in groups.items():
            commands = group['commands']
            if not commands or any(type(cmd) is not int or cmd <= 0 for cmd in commands):
                raise ValueError('Invalid login commands: '+name)
            if assigned.intersection(commands) or PROTECTED.intersection(commands):
                raise ValueError('Duplicate or protected login commands: '+name)
            assigned.update(commands)
        # Boss data is scoped to membership, not the existence of an unscoped
        # guild_boss catalogue row inherited from the source database.
        # A successful empty 33001 makes GuildAction index ClubBossCfg[0].
        if not db.query('SELECT 1 FROM club WHERE uid=? AND club_id>0 LIMIT 1', (uid,)):
            self.omitted[33001] = ('guild_boss', 'not_in_club')
        if self.mode == 'full_initial':
            return
        level = int(db.get('users', uid)['level'])
        cleared = {int(row['id']) for row in db.query(
            'SELECT id FROM chapter WHERE uid=? AND clear_times>0', (uid,))}
        for name, group in groups.items():
            if name in included:
                continue
            reason = self.lock_reason(group, level, cleared)
            if reason:
                for cmd in group['commands']:
                    self.omitted[cmd] = (name, reason)

    @staticmethod
    def lock_reason(group, level, cleared):
        if group.get('system_hide') == 1:
            return 'client_system_hide'
        # Matches JumpTools.IsConditionLocked: conditions are AND; battle
        # alternatives within one condition are OR. Unknown conditions keep
        # the packet, rather than guessing that a system is locked.
        for condition in group['open_condition']:
            kind = condition[0]
            if kind == 0:
                return 'client_lock'
            if kind == 1 and level < condition[1]:
                return 'level<'+str(condition[1])
            if kind == 2 and not cleared.intersection(condition[1:]):
                return 'stage='+('/'.join(map(str, condition[1:])))
        return None

    def allows(self, cmd):
        return cmd not in self.omitted

    def allows_empty_default(self, cmd, data):
        """Only called when no account-state generator exists for a packet.

        A synthetic zero activity/template is not an empty activity snapshot:
        client consumers use those identifiers directly as configuration keys.
        """
        if self.mode != 'unlocked':
            return True
        zero_keys = [key for key in ('activity_id', 'template_id')
                     if key in data and data[key] == 0]
        if not zero_keys:
            return True
        if cmd in PROTECTED:
            raise RuntimeError(f'sc_{cmd} required login state has no valid identity')
        self.empty_omissions[cmd] = zero_keys
        return False

    def report(self, log):
        if self.mode == 'legacy':
            return
        groups = {}
        for cmd, (name, reason) in self.omitted.items():
            groups.setdefault((name, reason), []).append(cmd)
        log(f'[login-policy] mode={self.mode}; omitted_groups={len(groups)}', 'INFO')
        for (name, reason), commands in groups.items():
            log(f'[login-policy] omit {name} ({reason}): '+','.join(map(str, commands)), 'INFO')
