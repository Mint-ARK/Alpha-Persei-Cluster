"""Export native legacy music reward targets; client sources stay read-only."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def build():
    config = ROOT / 'decompiled_v2/x64/game/config'
    music = config / 'activitymusiccfg.lua'
    settings = config / 'gamesetting.lua'
    text = music.read_text('utf-8-sig')
    targets = {}
    for body in re.findall(r'^\t(?:\[\d+\] = )?\{\n(.*?)^\t\}', text, re.M | re.S):
        mid = re.search(r'^\t\tid = (\d+),', body, re.M)
        target = re.search(r'^\t\ttarget = (\d+),', body, re.M)
        if mid and target:
            targets[mid[1]] = int(target[1])
    raw = settings.read_text('utf-8-sig')
    grade = re.search(r'\tattach_music_grade = \{.*?\bvalue = \{(.*?)\}', raw, re.S)
    grades = [int(v) for v in re.findall(r'\d+', grade[1])]
    assert len(targets) == 166 and grades == [97, 90, 80, 60, 50, 0]
    return dict(targets=targets, grades=grades,
                sources={str(p.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in [music, settings]})


if __name__ == '__main__':
    output = ROOT / 'v5_server/resident_music_contract.json'
    data = json.dumps(build(), ensure_ascii=False, indent=2) + '\n'
    if output.exists() and output.read_text('utf-8') != data:
        raise SystemExit('Back up the existing contract before replacing it')
    output.write_text(data, encoding='utf-8')
    print('Exported 166 song targets and six native grades')
