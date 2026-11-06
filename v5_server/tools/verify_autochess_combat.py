"""Isolated numeric buff/renderer projections. No databases or sockets."""
from pathlib import Path
import os,sys,unittest
from copy import deepcopy
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['ACCOUNT_DB_PATH']=':memory:'
import autochess_service as ac
from autochess_combat import Combat
import codec


class CombatTests(unittest.TestCase):
    def setUp(self):self.svc=ac.AutoChessService()
    def piece(self,cid,uid,index=1,level=1,atk=None,hp=None,buffs=None):
        cfg=self.svc.get_chess_cfg(cid) or {};body=cfg.get('body') or [1,1]
        exp=1 if level==1 else (cfg.get('exp') or [3,6])[level-2]
        ids=(cfg.get('level_buffs') or [[]])[level-1] if buffs is None else buffs
        ids=ids if isinstance(ids,list) else [ids]
        return dict(unique_id=uid,chess_id=cid,index=index,exp=exp,star=level,
                    atk=body[0] if atk is None else atk,hp=body[1] if hp is None else hp,
                    chess_attr_list=[dict(key=3,value=exp)],
                    buff_list=[dict(buff_id=bid,unique_id=uid*100+i+1) for i,bid in enumerate(ids)])
    def run_battle(self,p,e,session=None):
        result,actions=self.svc._simulate_round_battle(p,e,session)
        raw=codec.encode('sc_89113',dict(game_type=0,battle_uid=123,battle_info=dict(group_id=1,action_list=actions)))
        decoded=codec.decode(raw,'sc_89113')['battle_info'].get('action_list',[])
        self.assertEqual(len(actions),len(decoded))
        self.assert_replay(p,e,result,decoded)
        return result,decoded
    def assert_replay(self,players,enemies,result,actions):
        state={}
        def add(piece,side):
            attrs={x['key']:x['value'] for x in piece.get('chess_attr_list',[])}
            body=(self.svc.get_chess_cfg(piece['chess_id']) or {}).get('body') or [1,1]
            self.assertNotIn(piece['unique_id'],state)
            state[piece['unique_id']]=dict(side=side,index=piece['index'],hp=piece.get('hp',attrs.get(2,body[1])))
        for side,team in enumerate((players,enemies)):
            for piece in team:add(piece,side)
        for a in actions:
            kind=a['action_type']
            if kind==1:
                for pair in a['action_move_info']['action_list']:state[pair['key']]['index']=pair['value']
            elif kind==2:
                for pair in a['action_battle_info']['action_list']:
                    self.assertGreater(state[pair['key']]['hp'],0)
                    state[pair['key']]['hp']+=pair['value']
            elif kind==3:
                effect=a['action_effect_info']
                if effect['effect_enum'] in (1,2):
                    for target in effect['target_list']:
                        state[target['id']]['hp']+=sum(p['value'] for p in target['update_list'] if p['key']==2)
                elif effect['effect_enum']==3:
                    for piece in effect['call_info']['chess_list']:add(piece,effect['call_info']['user_id'])
            elif kind==5:
                for uid in a['action_dead_info']['uid_list']:
                    self.assertLessEqual(state[uid]['hp'],0);del state[uid]
            for side in (0,1):
                slots=[p['index'] for p in state.values() if p['side']==side]
                self.assertEqual(len(slots),len(set(slots)))
                self.assertTrue(all(1<=i<=7 for i in slots))
        live=[any(p['side']==side and p['hp']>0 for p in state.values()) for side in (0,1)]
        self.assertEqual(result,1 if live[0] else 2 if live[1] else 0)
    def effects(self,actions,bid=None,effect=None):
        return [a['action_effect_info'] for a in actions if a['action_type']==3 and
                (bid is None or a['action_effect_info']['buff_uid']==bid) and
                (effect is None or a['action_effect_info']['effect_enum']==effect)]

    def test_simultaneous_death_is_native_draw_zero(self):
        self.assertEqual(self.run_battle([self.piece(999,1)],[self.piece(999,2)])[0],0)
    def test_empty_sides_have_consistent_results(self):
        p=self.piece(999,1)
        self.assertEqual(self.run_battle([],[])[0],0)
        self.assertEqual(self.run_battle([p],[])[0],1)
        self.assertEqual(self.run_battle([],[p])[0],2)
    def test_budget_failure_never_becomes_win(self):
        with patch.object(Combat,'MAX_ROUNDS',2),self.assertRaises(ac.OperationError):
            self.run_battle([self.piece(999,1,atk=0)],[self.piece(999,2,atk=0)])
    def test_101_does_not_gain_attack_on_damage(self):
        _,a=self.run_battle([self.piece(101,1,atk=1,hp=5)],[self.piece(999,2,atk=1,hp=9)])
        self.assertFalse(self.effects(a,bid=101))
    def test_104_buffs_rear_only_on_attack(self):
        _,a=self.run_battle([self.piece(104,1,level=2,atk=1,hp=3),self.piece(999,3,index=2,hp=10)],
                            [self.piece(999,2,atk=1,hp=50)])
        effects=self.effects(a,bid=101,effect=1);self.assertTrue(effects)
        self.assertTrue(all(x['target_list'][0]['id']==3 for x in effects))
        self.assertTrue(all(x['target_list'][0]['update_list']==[dict(key=1,value=2),dict(key=2,value=2)] for x in effects))
    def test_104_without_rear_does_not_buff_self(self):
        _,a=self.run_battle([self.piece(104,1,atk=1,hp=4)],[self.piece(999,2,atk=1,hp=10)])
        self.assertFalse(self.effects(a,bid=101))
    def test_110_permanent_attack_equals_action_sum_without_hp_bonus(self):
        p=self.piece(110,1,atk=1,hp=9);session=dict(board_chess=[deepcopy(p)])
        _,a=self.run_battle([p],[self.piece(999,2,atk=2,hp=30)],session)
        effects=self.effects(a,bid=101,effect=1);self.assertTrue(effects)
        self.assertTrue(all(x['target_list'][0]['update_list'][1]['value']==0 for x in effects))
        delta=sum(x['target_list'][0]['update_list'][0]['value'] for x in effects)
        self.assertEqual(session['board_chess'][0]['atk'],1+delta);self.assertEqual(session['board_chess'][0]['hp'],9)
    def test_404_permanent_growth_keeps_only_original_instances(self):
        p=[self.piece(404,1,atk=1,hp=4),self.piece(999,3,index=2,atk=1,hp=8)]
        session=dict(board_chess=deepcopy(p));_,a=self.run_battle(p,[self.piece(999,2,atk=1,hp=50)],session)
        effects=self.effects(a,bid=101,effect=1);self.assertTrue(effects)
        delta=sum(x['target_list'][0]['update_list'][0]['value'] for x in effects)
        self.assertEqual(session['board_chess'][1]['atk'],1+delta)
        self.assertEqual(session['board_chess'][1]['hp'],8+delta)
        self.assertEqual(len(session['board_chess']),2)
    def test_303_temporary_growth_does_not_persist(self):
        p=self.piece(303,1,atk=1,hp=4);session=dict(board_chess=[deepcopy(p)])
        self.run_battle([p],[self.piece(999,2,atk=3,hp=10)],session)
        self.assertEqual(session['board_chess'],[p])
    def test_309_summons_level_specific_template(self):
        for level,cid in [(1,3091),(2,3092),(3,3093)]:
            _,a=self.run_battle([self.piece(309,1,level=level,atk=1,hp=4)],[self.piece(999,2,atk=2,hp=20)])
            calls=self.effects(a,effect=3);self.assertTrue(calls)
            self.assertEqual(calls[0]['call_info']['chess_list'][0]['chess_id'],cid)
    def test_210_uses_config_damage_at_every_level(self):
        for level,damage in [(1,3),(2,6),(3,9)]:
            _,a=self.run_battle([self.piece(210,1,level=level,atk=1,hp=9)],[self.piece(999,2,atk=1,hp=100)])
            damage_frames=self.effects(a,bid=101,effect=2);self.assertTrue(damage_frames)
            self.assertTrue(all(x['target_list'][0]['update_list'][0]['value']==-damage for x in damage_frames))
    def test_208_308_description_does_not_give_self_initial_shield(self):
        for cid in (208,308):
            _,a=self.run_battle([self.piece(cid,1,atk=1,hp=5)],[self.piece(999,2,atk=2,hp=20)])
            first=next(x for x in a if x['action_type']==2)
            self.assertEqual(first['action_battle_info']['action_list'][0]['value'],-2)
    def test_initial_shield_consumed_with_real_remove_frame(self):
        _,a=self.run_battle([self.piece(999,1,hp=5,buffs=[3])],[self.piece(999,2,atk=2,hp=9)])
        removed=[x for x in a if x['action_type']==7]
        self.assertEqual(removed[0]['remove_effect_info']['buff_uid'],101)
        first=next(x for x in a if x['action_type']==2)
        self.assertEqual(first['action_battle_info']['action_list'][0]['value'],0)
    def test_307_recharge_requires_nonzero_damage(self):
        _,a=self.run_battle([self.piece(307,1,atk=1,hp=8)],[self.piece(999,2,atk=2,hp=20)])
        fights=[x for x in a if x['action_type']==2]
        self.assertEqual([x['action_battle_info']['action_list'][0]['value'] for x in fights[:3]],[0,-2,0])
        self.assertEqual(len(self.effects(a,effect=4)),1)
        self.assertEqual(len([x for x in a if x['action_type']==7]),2)
    def test_405_without_rear_does_not_self_summon(self):
        _,a=self.run_battle([self.piece(405,1,atk=1,hp=1)],[self.piece(999,2,atk=2,hp=20)])
        self.assertFalse(self.effects(a,effect=3))
    def test_405_inheritance_only_summons_when_recipient_dies(self):
        _,a=self.run_battle([self.piece(405,1,atk=1,hp=1),self.piece(999,3,index=2,atk=1,hp=1)],
                            [self.piece(999,2,atk=2,hp=100)])
        grants=self.effects(a,effect=4);self.assertEqual(grants[0]['add_buff_info'][0]['buff_info']['buff_id'],701)
        calls=[x for x in a if x['action_type']==3 and x['action_effect_info']['effect_enum']==3]
        self.assertTrue(calls);dead=next(x['action_id'] for x in a if x['action_type']==5 and 3 in x['action_dead_info']['uid_list'])
        self.assertGreater(calls[0]['action_id'],dead)
    def test_two_summons_have_distinct_slots_and_survivor_moves(self):
        _,a=self.run_battle([self.piece(205,1,hp=1),self.piece(999,3,index=2,hp=3)],
                            [self.piece(999,2,atk=3,hp=100)])
        calls=self.effects(a,effect=3)[0]['call_info']['chess_list']
        self.assertEqual([x['index'] for x in calls],[1,2])
        self.assertTrue(any(x['action_type']==1 and any(y['key']==3 for y in x['action_move_info']['action_list']) for x in a))
    def test_skill_killed_summon_removed_before_next_fight(self):
        c=Combat(self.svc,[self.piece(309,1,hp=10)],[self.piece(210,2,level=3,hp=100)],None)
        owner=c.teams[0][0];buff=owner['buffs'][0]
        c.spawn([3091],owner,buff,front=True);summon=c.teams[0][0]
        enemy=c.teams[1][0];c.damage(summon,9,enemy,enemy['buffs'][0]);c.flush();c.deaths()
        self.assertNotIn(summon,c.teams[0]);c.run()
        self.assertFalse(any(x['action_type']==2 and any(y['key']==summon['uid'] for y in x['action_battle_info']['action_list']) for x in c.actions))
    def test_208_summon_shield_has_explicit_add_buff(self):
        _,a=self.run_battle([self.piece(105,1,hp=1),self.piece(208,3,index=2,hp=10)],
                            [self.piece(999,2,atk=3,hp=50)])
        calls=self.effects(a,effect=3);summon=calls[0]['call_info']['chess_list'][0]['unique_id']
        self.assertTrue(any(x['add_buff_info'][0]['target_uid']==summon for x in self.effects(a,effect=4)))

    def test_403_copy_preserves_initial_stats_without_recursive_copy(self):
        _,a=self.run_battle([self.piece(403,1,atk=3,hp=6)],[self.piece(999,2,atk=2,hp=50)])
        copies=[c for e in self.effects(a,effect=3) for c in e['call_info']['chess_list']]
        self.assertEqual(len(copies),1)
        attrs={x['key']:x['value'] for x in copies[0]['chess_attr_list']}
        self.assertEqual(copies[0]['chess_id'],403)
        # The fixture has direct attributes; serialized copies must project
        # these into chess_attr_list for the client's summoned unit.
        self.assertEqual(attrs[1],3);self.assertEqual(attrs[2],6)
        self.assertNotIn(4034,[b['buff_id'] for b in copies[0]['buff_list']])

    def test_106_grows_on_deathrattle_not_plain_death(self):
        for cid,expected in ((105,True),(999,False)):
            _,a=self.run_battle([self.piece(cid,1,hp=1),self.piece(106,3,index=2,hp=20)],
                                [self.piece(999,2,atk=3,hp=100)])
            self.assertEqual(bool(self.effects(a,bid=301,effect=1)),expected)

    def test_206_growth_reaches_dead_instance_shop_and_future_summon(self):
        p=[self.piece(206,1,hp=1),self.piece(999,3,index=2,hp=30)]
        session=dict(board_chess=deepcopy(p),shop_items=[dict(id=206,shop_type=0,atk=2,hp=3),
                     dict(id=206,shop_type=1,atk=77,hp=88)])
        self.run_battle(p,[self.piece(999,2,atk=2,hp=60)],session)
        self.assertEqual(session['chess_bonus']['206'],[1,1])
        self.assertEqual(session['board_chess'][0]['hp'],2)
        self.assertEqual(session['shop_items'][0]['atk'],3)
        self.assertEqual(session['shop_items'][1]['atk'],77)
        c=Combat(self.svc,[self.piece(105,1)],[self.piece(999,2)],session)
        c.spawn([206],c.teams[0][0],c.teams[0][0]['buffs'][0])
        spawned=c.teams[0][1];body=self.svc.get_chess_cfg(206)['body']
        self.assertEqual((spawned['atk'],spawned['hp']),(body[0]+1,body[1]+1))

    def test_312_extra_damage_is_in_replay_delta(self):
        _,a=self.run_battle([self.piece(312,1,atk=1,hp=3)],[self.piece(999,2,atk=1,hp=20)])
        first=next(x for x in a if x['action_type']==2)
        self.assertEqual(first['action_battle_info']['action_list'][1]['value'],-2)

    def test_413_counter_is_shared_and_stops_at_eight(self):
        p=[self.piece(413,1,hp=100),self.piece(413,3,index=2,hp=100)]
        c=Combat(self.svc,p,[self.piece(999,2,hp=99999)],None)
        c.emit(11,None);c.flush()
        for _ in range(100):
            c.teams[0]=[f for f in c.teams[0] if f['cid']==413]
            c.renumber(0);c.emit(23,c.teams[0][-1]);c.flush()
        summons=[x for e in self.effects(c.actions,effect=3) for x in e['call_info']['chess_list']]
        self.assertEqual(len(summons),8);self.assertTrue(all(x['chess_id']==112 for x in summons))

    def test_disintegrate_consumes_only_on_actual_damage(self):
        c=Combat(self.svc,[self.piece(115,1,atk=1,hp=10)],[self.piece(999,2,hp=100,buffs=[3])],None)
        a,b=c.teams[0][0],c.teams[1][0]
        self.assertEqual(c.hit(b,1,a),0);self.assertTrue(c.is_deadly(a))
        self.assertEqual(c.hit(b,1,a),100);self.assertFalse(c.is_deadly(a))

    def test_friendly_fire_does_not_count_as_enemy_damage_or_kill(self):
        c=Combat(self.svc,[self.piece(113,1),self.piece(999,3,index=2,hp=1)],[self.piece(999,2)],None)
        c.hit(c.teams[0][1],2,c.teams[0][0])
        self.assertFalse(any(e[0] in (22,23) for e in c.events))

    def test_boss_544_removes_only_dead_original_uid(self):
        p=[self.piece(105,1,hp=1),self.piece(999,3,index=2,hp=2000,atk=1000)]
        session=dict(board_chess=deepcopy(p))
        self.run_battle(p,[self.piece(17,2,level=5)],session)
        self.assertEqual([x['unique_id'] for x in session['board_chess']],[3])

    def test_523_doubles_deathrattles_without_recursion(self):
        p=[self.piece(105,1,hp=1),self.piece(16,3,index=2,level=4,atk=1,hp=10)]
        _,a=self.run_battle(p,[self.piece(999,2,atk=2,hp=100)])
        calls=[x for e in self.effects(a,effect=3) for x in e['call_info']['chess_list']]
        self.assertEqual(len(calls),2)

    def test_unknown_active_effect_fails_before_settlement(self):
        c=Combat(self.svc,[self.piece(999,1)],[self.piece(999,2)],None)
        with self.assertRaises(ac.OperationError):
            c.effect(c.teams[0][0],dict(unique_id=1),dict(id=999999,action_type=999,param=[]),None,None)

    def test_all_native_normal_chess_levels_and_enemy_managers_complete(self):
        import random
        random.seed(413)
        cfg=self.svc.cfg
        for cid,definition in cfg['chess'].items():
            if definition.get('type')!=0 and int(cid) not in (11,12,13,14,15,16,17,19,20):continue
            for level in range(1,len(definition.get('level_buffs') or [[]])+1):
                with self.subTest(cid=cid,level=level):
                    p=[self.piece(101,1,atk=1,hp=1,buffs=[8]),self.piece(int(cid),3,index=2,level=level,hp=20),
                       self.piece(101,4,index=3,atk=3,hp=8,buffs=[8])]
                    result,a=self.run_battle(p,[self.piece(999,2,atk=4,hp=120)])
                    self.assertIn(result,(0,1,2));self.assertTrue(a)

    def test_summon_capacity_truncates_to_seven_unique_slots(self):
        p=[self.piece(999,i,index=i,hp=10) for i in range(1,7)]
        c=Combat(self.svc,p,[self.piece(999,20)],None)
        c.spawn([2051,2054],c.teams[0][0],dict(unique_id=555),front=True)
        self.assertEqual(len(c.teams[0]),7)
        self.assertEqual([f['index'] for f in c.teams[0]],list(range(1,8)))
        self.assertEqual(c.spawn([2051],c.teams[0][0],dict(unique_id=555)),0)

    def test_105_native_phase1_enemy_teams_replay_and_timing(self):
        import json,time
        samples=[];counts=[]
        for tid,team in self.svc.cfg['enemy_teams'].items():
            if team['group_id']>=400:continue
            with self.subTest(team_id=tid):
                enemy=self.svc._enemy_records(int(tid))
                started=time.perf_counter()
                result,actions=self.run_battle([self.piece(101,1,atk=1000,hp=5000,buffs=[8])],enemy)
                samples.append((time.perf_counter()-started)*1000);counts.append(len(actions))
                self.assertEqual(result,1)
        self.assertEqual(len(samples),105)
        samples.sort()
        print('Native enemy smoke '+json.dumps(dict(samples=len(samples),mean_ms=round(sum(samples)/len(samples),3),
            p95_ms=round(samples[int(len(samples)*.95)],3),max_ms=round(max(samples),3),max_actions=max(counts))))


if __name__=='__main__':unittest.main(verbosity=2)
