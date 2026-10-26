"""Chapter map progress is projected from durable stage clears and clue reads.

Historical small replay-node IDs are not map event IDs. Replay uses a separate
cursor: the client reconstructs temporary completion strictly before its time.
"""
import json
import time
import uuid
from pathlib import Path


class ChapterV2MapService:
    def __init__(self):
        cfg = json.loads(Path(__file__).with_name('chapter_v2_map_cfg.json').read_text('utf-8-sig'))
        self.maps = {int(k): v for k, v in cfg['maps'].items()}
        self.locations = {int(k): v for k, v in cfg['locations'].items()}
        self.events = {int(k): v for k, v in cfg['events'].items()}
        self.conditions = {int(k): v for k, v in cfg['conditions'].items()}
        self.stage_events = {}
        for eid, event in self.events.items():
            if event['type'] in (1, 2):
                self.stage_events.setdefault(int(event['params'][0]), []).append(eid)

    def has_stage(self, stage_id):
        return int(stage_id or 0) in self.stage_events

    def _state(self, db, uid):
        rows = db.query('SELECT now_location, read_events_json FROM chapter_v2_map_state WHERE uid=?', (uid,))
        if not rows:
            return 0, set()
        location = int(rows[0]['now_location'] or 0)
        if location not in self.locations:
            location = 0
        # Invalid stored JSON is a read failure, not an empty account snapshot.
        saved = json.loads(rows[0]['read_events_json'])
        if not isinstance(saved, list):
            raise ValueError('Chapter map read events must be a list')
        reads = {int(eid) for eid in saved if int(eid) in self.events and self.events[int(eid)]['type'] == 3}
        return location, reads

    def _completed(self, db, uid, reads):
        completed = set(reads)
        # StageService writes these main-plot stages to chapter, including pure story.
        for row in db.query('SELECT id FROM chapter WHERE uid=? AND clear_times>0', (uid,)):
            completed.update(self.stage_events.get(int(row['id']), ()))
        return completed

    def payload(self, db, uid):
        location, reads = self._state(db, uid)
        completed = self._completed(db, uid, reads)
        track = self._backtrack(db, uid)
        backtrack_list = []
        if track:
            location = track['now_location']
            temporary = self._temporary(completed, track)
            backtrack_list.append(dict(map_id=track['map_id'], start_event=track['start_event'],
                backtrack_event_list=sorted(eid for eid in temporary
                                           if self._event_chapter(eid) == self._track_chapter(track))))
        return dict(now_location=location, event_list=sorted(completed), backtrack_list=backtrack_list)

    def conditions_met(self, db, uid, ids, completed, permanent=None):
        permanent = completed if permanent is None else permanent
        for cid in ids:
            row = self.conditions.get(int(cid))
            if row is None:
                return False
            kind, params = int(row['type']), row['params']
            if kind == 12606:
                ok = all(int(eid) in permanent for eid in params)
            elif kind == 12602:
                ok = all(int(eid) in completed for eid in params)
            elif kind == 12604:
                ok = any(int(eid) in completed for eid in params)
            elif kind == 12603:
                from stage_service import StageService
                stages = StageService.get_instance(db=db)
                chapter = int(params[0])
                ok = str(chapter) in stages.chapters and stages.is_chapter_unlocked(uid, chapter)[0]
            else:
                ok = False
            if not ok:
                return False
        return True

    def _save(self, db, uid, location, reads):
        db.execute('''INSERT INTO chapter_v2_map_state(uid,now_location,read_events_json,update_ts)
                      VALUES(?,?,?,?) ON CONFLICT(uid) DO UPDATE SET
                      now_location=excluded.now_location,read_events_json=excluded.read_events_json,
                      update_ts=excluded.update_ts''',
                   (uid, int(location), json.dumps(sorted(reads)), int(time.time())))

    def read_event(self, db, uid, event_id):
        event_id = int(event_id)
        event = self.events.get(event_id)
        if not event or event['type'] != 3:
            raise ValueError('Only configured supplement events can be read')
        location, reads = self._state(db, uid)
        if event_id in reads:
            return
        completed = self._completed(db, uid, reads)
        track = self._backtrack(db, uid)
        temporary = self._temporary(completed, track) if track else completed
        gates = event['display_condition'] + event['unlock_condition']
        if not self.conditions_met(db, uid, gates, temporary, permanent=completed):
            raise ValueError('Chapter map event is locked')
        reads.add(event_id)
        self._save(db, uid, location, reads)

    def move_location(self, db, uid, location_id):
        location_id = int(location_id)
        location = self.locations.get(location_id)
        if location is None:
            raise ValueError('Unknown chapter map location')
        _, reads = self._state(db, uid)
        completed = self._completed(db, uid, reads)
        track = self._backtrack(db, uid)
        map_cfg = self.maps[location['map']]
        same_timeline = track and map_cfg['chapter_client_id'] == self._track_chapter(track)
        temporary = self._temporary(completed, track) if same_timeline else completed
        gates = map_cfg['unlock_condition'] + location['first_unlock_condition']
        if not self.conditions_met(db, uid, gates, temporary, permanent=completed):
            raise ValueError('Chapter map location is locked')
        if same_timeline:
            db.execute('UPDATE chapter_v2_backtrack SET now_location=?,update_ts=? WHERE uid=?',
                       (location_id, int(time.time()), uid))
        else:
            if track:
                db.execute('DELETE FROM chapter_v2_backtrack WHERE uid=?', (uid,))
            self._save(db, uid, location_id, reads)

    def _event_map(self, event_id):
        return self.locations[self.events[event_id]['location']]['map']

    def _event_chapter(self, event_id):
        return self.maps[self._event_map(event_id)]['chapter_client_id']

    def _track_chapter(self, track):
        return self.maps[track['map_id']]['chapter_client_id']

    def _backtrack(self, db, uid):
        rows = db.query('SELECT map_id,start_event,now_location,token FROM chapter_v2_backtrack WHERE uid=?', (uid,))
        if not rows:
            return None
        row = dict(rows[0])
        for key in ('map_id', 'start_event', 'now_location'):
            row[key] = int(row[key])
        if (row['map_id'] not in self.maps or row['start_event'] not in self.events
                or row['now_location'] not in self.locations or not row['token']):
            raise ValueError('Invalid persisted chapter backtrack')
        chapter = self._track_chapter(row)
        if (self._event_map(row['start_event']) != row['map_id']
                or self.maps[self.locations[row['now_location']]['map']]['chapter_client_id'] != chapter):
            raise ValueError('Backtrack cursor/location chapter mismatch')
        return row

    def _temporary(self, completed, track):
        chapter = self._track_chapter(track)
        cutoff = self.events[track['start_event']]['time_stamp']
        return {eid for eid in completed if self._event_chapter(eid) == chapter
                and self.events[eid]['time_stamp'] < cutoff}

    def track_event(self, db, uid, event_id):
        event_id = int(event_id)
        if event_id not in self.events or self.events[event_id]['type'] not in (1, 2):
            raise ValueError('Unknown replayable chapter event')
        _, reads = self._state(db, uid)
        completed = self._completed(db, uid, reads)
        if event_id not in completed:
            raise ValueError('Backtrack event has not been completed')
        map_id = self._event_map(event_id)
        condition = self.maps[map_id]['timeline_unlock_condition']
        if condition and not self.conditions_met(db, uid, [condition], completed):
            raise ValueError('Chapter timeline is locked')
        current = self._backtrack(db, uid)
        if current and current['start_event'] == event_id:
            return
        db.execute('''INSERT INTO chapter_v2_backtrack(uid,map_id,start_event,now_location,token,update_ts)
                      VALUES(?,?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET map_id=excluded.map_id,
                      start_event=excluded.start_event,now_location=excluded.now_location,
                      token=excluded.token,update_ts=excluded.update_ts''',
                   (uid, map_id, event_id, self.events[event_id]['location'], uuid.uuid4().hex, int(time.time())))

    def quit_tracking(self, db, uid, map_id):
        map_id = int(map_id)
        if map_id not in self.maps:
            raise ValueError('Unknown backtrack map')
        track = self._backtrack(db, uid)
        # The client creates trackInfo for every map in this chapter.
        if track and self.maps[map_id]['chapter_client_id'] != self._track_chapter(track):
            raise ValueError('Map is not in the active backtrack chapter')
        db.execute('DELETE FROM chapter_v2_backtrack WHERE uid=?', (uid,))

    def battle_token(self, db, uid, stage_id):
        if not self.has_stage(stage_id):
            return ''
        track = self._backtrack(db, uid)
        if track and self._event_chapter(self.stage_events[int(stage_id)][0]) == self._track_chapter(track):
            return track['token']
        return ''

    def on_stage_clear(self, db, uid, stage_id, token):
        if not token or not self.has_stage(stage_id):
            return False
        track = self._backtrack(db, uid)
        if not track or token != track['token']:
            return False
        eid = self.stage_events[int(stage_id)][0]
        event = self.events[eid]
        chapter = self._track_chapter(track)
        if (self._event_chapter(eid) != chapter
                or event['time_stamp'] < self.events[track['start_event']]['time_stamp']):
            return False
        # Only a durable successful clear may move the temporary cursor.
        _, reads = self._state(db, uid)
        if eid not in self._completed(db, uid, reads):
            return False
        later = [(row['time_stamp'], key) for key, row in self.events.items()
                 if row['type'] in (1, 2) and self._event_chapter(key) == chapter
                 and row['time_stamp'] > event['time_stamp']]
        if later:
            next_event = min(later)[1]
            changed = db.execute('''UPDATE chapter_v2_backtrack SET map_id=?,start_event=?,update_ts=?
                                    WHERE uid=? AND token=? AND start_event=?''',
                       (self._event_map(next_event), next_event, int(time.time()), uid, token, track['start_event']))
        else:
            # No later replay node remains; restore the saved normal-map position.
            changed = db.execute('DELETE FROM chapter_v2_backtrack WHERE uid=? AND token=? AND start_event=?',
                                 (uid, token, track['start_event']))
        return changed.rowcount > 0


service = ChapterV2MapService()
