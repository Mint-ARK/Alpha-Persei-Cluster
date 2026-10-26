"""Configuration-backed sign rewards and cumulative-login popup eligibility."""
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _version_rewards():
    return json.loads(Path(__file__).with_name('seven_day_sign_versions.json').read_text('utf-8'))


def versioned_seven_day_rewards(activity_id, res_version=None):
    import res_version_manager
    version = res_version_manager.get_version_config(res_version)
    profiles = _version_rewards()['profiles']
    matching = [int(build) for build, data in profiles.items()
                if int(build) <= int(version['version'])
                and data['theme_id'] == int(version['theme_id'])]
    if not matching:
        return None
    return profiles[str(max(matching))]['activities'].get(str(int(activity_id)))


@lru_cache(maxsize=1)
def accumulate_rewards():
    return json.loads(Path(__file__).with_name('accumulate_login_cfg.json').read_text('utf-8'))


def claimable_accumulate_ids(db, uid, version, login_days, award_ids=None):
    claimed = set(award_ids if award_ids is not None else
                  (int(row['award_id']) for row in db.query(
                      'SELECT award_id FROM accumulate_sign_award WHERE uid=?', (uid,))))
    return [int(aid) for aid, item in accumulate_rewards().items()
            if int(item['version']) == int(version) and int(item['num']) <= int(login_days)
            and int(aid) not in claimed]


def accumulate_payload(db, uid, *, initial_version=None):
    row = db.get('accumulate_sign', uid)
    if not row and initial_version is None:
        return None
    row = row or {}
    version = int(row.get('version') or initial_version or 1)
    if version not in {int(item['version']) for item in accumulate_rewards().values()}:
        raise ValueError('Unknown saved accumulate sign version')
    days = int(row.get('login_days') or 0)
    awards = [int(item['award_id']) for item in db.query(
        'SELECT award_id FROM accumulate_sign_award WHERE uid=? ORDER BY award_id', (uid,))]
    # Old saves may carry open_sign=1 with no eligible reward. A display flag
    # cannot make an unearned/already claimed tier trigger the first-open popup.
    opened = bool(row.get('open_sign', 0)) and bool(
        claimable_accumulate_ids(db, uid, version, days, awards))
    return dict(version=version, open_sign=opened, login_days=days, award_ids=awards)
