"""Chapter19 detector, archive and watch state, separate from stage completion."""
import json
import random
import time
from pathlib import Path


class Chapter19Service:
    def __init__(self):
        cfg = json.loads(Path(__file__).with_name('chapter19_cfg.json').read_text('utf-8-sig'))
        self.maps = {int(k): v for k, v in cfg['maps'].items()}
        self.locations = {int(k): v for k, v in cfg['locations'].items()}
        self.archives = {int(k): v for k, v in cfg['archives'].items()}
        self.settings = cfg['settings']
        self.location_maps = {lid: mid for mid, row in self.maps.items() for lid in row['location_list']}
        self.clue_locations = {cid: lid for lid, row in self.locations.items() for cid in row['clue']}
        self.event_locations = {eid: lid for lid, row in self.locations.items() for eid in row['event_list']}
        self.days = sorted({m['day'] for m in self.maps.values()})

    def _load(self, db, uid):
        rows = db.query('SELECT state_json FROM chapter19_state WHERE uid=?', (uid,))
        state = json.loads(rows[0]['state_json']) if rows else dict(scans=[], clues={}, events=[], days=[])
        if (not isinstance(state, dict) or set(state) != {'scans', 'clues', 'events', 'days'}
                or not all(isinstance(state[k], list) for k in ('scans', 'events', 'days'))
                or not isinstance(state['clues'], dict)):
            raise ValueError('Invalid chapter19 state')
        if (any(lid not in self.locations or not self.locations[lid]['need_scan'] for lid in state['scans'])
                or any(eid not in self.event_locations for eid in state['events'])
                or any(day not in self.days for day in state['days'])
                or any(int(lid) not in self.locations or cid not in self.locations[int(lid)]['clue']
                       for lid, cid in state['clues'].items())):
            raise ValueError('Unknown chapter19 state IDs')
        return state

    def _save(self, db, uid, state):
        db.execute('''INSERT INTO chapter19_state(uid,state_json,update_ts) VALUES(?,?,?)
            ON CONFLICT(uid) DO UPDATE SET state_json=excluded.state_json,update_ts=excluded.update_ts''',
            (uid, json.dumps(state, sort_keys=True), int(time.time())))

    def _clears(self, db, uid):
        return {int(r['id']) for r in db.query('SELECT id FROM chapter WHERE uid=? AND clear_times>0', (uid,))}

    def _day_available(self, day, clears):
        index = self.days.index(day)
        if index == 0:
            return True
        previous = self.days[index - 1]
        return all(sid in clears for m in self.maps.values() if m['day'] == previous
                   for lid in m['location_list'] for sid in self.locations[lid]['stage_list'])

    def _location_available(self, lid, clears, click=False):
        row = self.locations[lid]
        if not self._day_available(self.maps[self.location_maps[lid]]['day'], clears):
            return False
        if row['unlock_stage_id'] and row['unlock_stage_id'] not in clears:
            return False
        # Client evaluates explicit show before hide.
        if row['show_by_stage_id'] not in clears and row['hide_by_stage_id'] in clears:
            return False
        if click and row['can_not_click_until_unlock_fog'] and self.settings['chapter19_fog_dissipation'] not in clears:
            return False
        return True

    def payload(self, db, uid):
        state = self._load(db, uid)
        info = []
        # Include empty maps: ScanClueLocationData indexes the map table directly.
        for mid, row in sorted(self.maps.items()):
            ids = set(row['location_list'])
            info.append(dict(map_id=mid,
                scan_location_list=sorted(ids.intersection(state['scans'])),
                clue_location_list=[dict(location_id=int(lid), clue_id=cid)
                                    for lid, cid in sorted(state['clues'].items(), key=lambda p: int(p[0]))
                                    if int(lid) in ids],
                event_list=sorted(eid for eid in state['events'] if self.event_locations[eid] in ids),
                clue_list=[]))
        return dict(map_info=info, detector_info=[dict(chapter_id=10119, day=day, status=1)
                                                 for day in sorted(set(state['days']))])

    def scan(self, db, uid, mid):
        mid = int(mid)
        row = self.maps[mid]
        clears = self._clears(db, uid)
        if self.settings['chapter19_scan_unlock'] not in clears or not self._day_available(row['day'], clears):
            raise ValueError('Chapter detector is locked')
        state = self._load(db, uid)
        found = [lid for lid in row['location_list'] if self.locations[lid]['need_scan']
                 and self._location_available(lid, clears)
                 and str(lid) not in state['clues']]
        fresh = [lid for lid in found if lid not in state['scans']]
        if fresh:
            state['scans'] = sorted(set(state['scans']).union(fresh))
            self._save(db, uid, state)
        # Returning a collected location would overwrite the client's clue with 0.
        return dict(result=0, location_id_list=found)

    def collect(self, db, uid, lid):
        lid = int(lid)
        row = self.locations[lid]
        if row['type'] != 2 or not row['need_scan'] or not row['clue']:
            raise ValueError('Not a detector clue location')
        state = self._load(db, uid)
        cid = state['clues'].get(str(lid))
        if cid is None:
            if lid not in state['scans'] or not self._location_available(lid, self._clears(db, uid), click=True):
                raise ValueError('Clue location is locked or unscanned')
            cid = self._choose_clue(row['clue'])
            state['clues'][str(lid)] = cid
            self._save(db, uid, state)
        return dict(result=0, map_id=self.location_maps[lid],
                    clue_location=dict(location_id=lid, clue_id=cid))

    def _choose_clue(self, candidates):
        # Project policy confirmed by the user: uniform, persisted on first obtain.
        return random.choice(candidates)

    def read_clue(self, db, uid, cid):
        cid = int(cid)
        lid = self.clue_locations[cid]
        row = self.locations[lid]
        if row['need_scan']:
            raise ValueError('Detector clues must be obtained through collection')
        state = self._load(db, uid)
        if state['clues'].get(str(lid)) == cid:
            return
        if not self._location_available(lid, self._clears(db, uid), click=True):
            raise ValueError('Clue location is locked')
        state['clues'][str(lid)] = cid
        self._save(db, uid, state)

    def read_event(self, db, uid, eid):
        eid = int(eid)
        lid = self.event_locations[eid]
        state = self._load(db, uid)
        if eid in state['events']:
            return
        clears = self._clears(db, uid)
        gate = self.archives[eid]['unlock_by_stage_id']
        if (gate and gate not in clears) or not self._location_available(lid, clears, click=True):
            raise ValueError('Chapter archive is locked')
        state['events'].append(eid)
        self._save(db, uid, state)

    def operate_day(self, db, uid, detector):
        chapter, day, status = (int(detector[k]) for k in ('chapter_id', 'day', 'status'))
        if chapter != 10119 or day not in self.days or status != 1:
            raise ValueError('Unknown chapter day operation')
        state = self._load(db, uid)
        if day in state['days']:
            return
        clears = self._clears(db, uid)
        gate = (self.settings['chapter19_activate'] if day == self.days[0] else
                self.settings['chapter19_exit'] if day == self.days[self.settings['chapter19_timeline'] - 1] else 0)
        if not self._day_available(day, clears) or (gate and gate not in clears):
            raise ValueError('Chapter day is locked')
        state['days'].append(day)
        self._save(db, uid, state)


service = Chapter19Service()
