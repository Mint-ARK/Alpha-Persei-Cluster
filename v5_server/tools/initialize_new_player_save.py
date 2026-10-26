"""Initialize the experimental single-account save; --apply is required."""
import argparse
import json
import sys
from pathlib import Path

SERVER=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SERVER))
from account_db import AccountDB
from new_player_service import config

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--uid',type=int,required=True)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    if args.apply:
        if not args.report or args.report.exists() or args.report.resolve()==args.db.resolve():
            parser.error('Choose a new report path distinct from the database.')
        if not args.db.resolve().is_relative_to(SERVER/'saves/experimental'):
            parser.error('This experiment only accepts databases under saves/experimental/.')
        report=AccountDB.initialize_new_player_file(args.db,args.uid)
        args.report.parent.mkdir(parents=True,exist_ok=True)
        with args.report.open('x',encoding='utf-8') as stream:
            json.dump(report,stream,ensure_ascii=False,indent=2)
        print('Initialized level 1, hero',report['starter_hero'],'with unchanged schema; backup:',report['backup'])
    else:
        print('DRY RUN: level=1, starter=',config()['starter_hero']['id'],', reset player rows; retain schema and shared config.')
