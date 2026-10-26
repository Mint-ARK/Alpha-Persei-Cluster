"""Shared sticker snapshots, ownership and read projections. No service startup."""
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path


class StickerWallError(ValueError):
    def __init__(self,message,code=2):
        super().__init__(message)
        self.code=code


@lru_cache(maxsize=1)
def load_rules():
    raw=json.loads(Path(__file__).with_name('sticker_wall_cfg.json').read_text('utf-8'))
    groups=('backgrounds','stickers','foregrounds')
    if (raw.get('schema')!=1 or any(not isinstance(raw.get(k),list) or not raw[k]
            or any(type(i) is not int or i<=0 for i in raw[k])
            or len(set(raw[k]))!=len(raw[k]) for k in groups)
            or raw.get('default_background') not in raw['backgrounds']
            or type(raw.get('max_stickers')) is not int or raw['max_stickers']<=0
            or type(raw.get('scale_min')) is not int or type(raw.get('scale_max')) is not int
            or not 0<raw['scale_min']<=raw['scale_max']<=10000):
        raise StickerWallError('贴纸墙配置无效')
    return raw


def read_extra(db,uid):
    rows=db.query('SELECT extra FROM users WHERE uid=?',(uid,))
    if len(rows)!=1:raise StickerWallError('账号不存在或不唯一',406)
    try:extra=json.loads(rows[0]['extra'] or '{}')
    except (ValueError,TypeError) as exc:raise StickerWallError('账号扩展数据无效，保留原存档') from exc
    if not isinstance(extra,dict):raise StickerWallError('账号扩展数据无效，保留原存档')
    return extra


def owned_assets(cards,rules):
    result={k:set() for k in ('backgrounds','stickers','foregrounds')}
    kinds={'sticker_bg':'backgrounds','sticker':'stickers','sticker_fg':'foregrounds','home_bg':'foregrounds'}
    for card in cards:
        group=kinds.get(card.get('kind'));iid=card.get('item_id')
        # home_bg compatibility is limited to IDs confirmed as sticker foregrounds.
        if group and card.get('obtained')==1 and iid in rules[group]:result[group].add(iid)
    # Preserve the server's existing default-background entitlement, not a Lua grant.
    result['backgrounds'].add(rules['default_background'])
    return result


def active_page_id(extra,game_user=None,rules=None,owned=None):
    rules=rules or load_rules()
    for value in (extra.get('sticker_show_page'),(game_user or {}).get('cur_sticker_bg')):
        if type(value) is int and value in rules['backgrounds'] and (owned is None or value in owned['backgrounds']):return value
    return rules['default_background']


def pages_from_extra(extra):
    pages=extra.get('sticker_show_info',[])
    if not isinstance(pages,list) or any(not isinstance(p,dict) for p in pages):
        raise StickerWallError('历史贴纸画册格式无效，保留原存档')
    return deepcopy(pages)


def projected_pages(extra,owned,rules=None):
    """Safe read projection only; never migrate or grant legacy template assets."""
    rules=rules or load_rules();result=[];seen_pages=set();seen_stickers=set();seen_fg=set()
    for raw in pages_from_extra(extra):
        pid=raw.get('page_id')
        if type(pid) is not int or pid not in owned['backgrounds'] or pid in seen_pages:continue
        seen_pages.add(pid);fg=raw.get('foreground',0)
        if type(fg) is not int or fg not in owned['foregrounds'] or fg in seen_fg:fg=0
        if fg:seen_fg.add(fg)
        entries=raw.get('sticker_display_info',[]);valid=[]
        if not isinstance(entries,list):entries=[]
        entries=sorted(entries,key=lambda x:x.get('layer',0) if isinstance(x,dict) and type(x.get('layer')) is int else 0)
        for entry in entries:
            if not isinstance(entry,dict):continue
            candidate=dict(entry,layer=1)
            try:item=_page(dict(page_id=pid,sticker_display_info=[candidate]),owned,rules)['sticker_display_info'][0]
            except StickerWallError:continue
            if item['sticker_id'] in seen_stickers:continue
            if len(valid)>=rules['max_stickers']:break
            seen_stickers.add(item['sticker_id']);item['layer']=len(valid)+1;valid.append(item)
        result.append(dict(page_id=pid,foreground=fg,sticker_display_info=valid))
    return result


def display_page(extra,game_user=None,rules=None,owned=None):
    rules=rules or load_rules();selected=active_page_id(extra,game_user,rules,owned)
    pages=projected_pages(extra,owned,rules) if owned is not None else pages_from_extra(extra)
    return next((p for p in pages if p.get('page_id')==selected),
                dict(page_id=selected,foreground=0,sticker_display_info=[]))


def _integer(value,name,minimum,maximum):
    if type(value) is not int or not minimum<=value<=maximum:
        raise StickerWallError(f'{name}无效')
    return value


def _page(raw,owned,rules):
    if not isinstance(raw,dict):raise StickerWallError('贴纸墙条目无效')
    pid=raw.get('page_id')
    if type(pid) is not int or pid not in rules['backgrounds']:raise StickerWallError('背景配置无效')
    if pid not in owned['backgrounds']:raise StickerWallError('尚未获得该贴纸背景',406)
    fg=raw.get('foreground',0)
    if type(fg) is not int or (fg and fg not in rules['foregrounds']):raise StickerWallError('前景配置无效')
    if fg and fg not in owned['foregrounds']:raise StickerWallError('尚未获得该贴纸前景',406)
    stickers=raw.get('sticker_display_info',[])
    if not isinstance(stickers,list) or len(stickers)>rules['max_stickers']:raise StickerWallError('贴纸数量无效')
    normalized=[]
    for s in stickers:
        if not isinstance(s,dict):raise StickerWallError('贴纸条目无效')
        sid=s.get('sticker_id')
        if type(sid) is not int or sid not in rules['stickers']:raise StickerWallError('贴纸配置无效')
        if sid not in owned['stickers']:raise StickerWallError('尚未获得该贴纸',406)
        normalized.append(dict(sticker_id=sid,
            location_x=_integer(s.get('location_x',0),'贴纸X坐标',0,10000),
            location_y=_integer(s.get('location_y',0),'贴纸Y坐标',0,10000),
            scale=_integer(s.get('scale',0),'贴纸缩放',rules['scale_min'],rules['scale_max']),
            rotate=_integer(s.get('rotate',0),'贴纸旋转',0,9999),
            layer=_integer(s.get('layer',0),'贴纸层级',1,len(stickers))))
    if sorted(s['layer'] for s in normalized)!=list(range(1,len(normalized)+1)):
        raise StickerWallError('贴纸层级须连续且唯一')
    normalized.sort(key=lambda s:s['layer'])
    return dict(page_id=pid,foreground=fg,sticker_display_info=normalized)


def _unique(pages):
    page_ids=set();stickers=set();foregrounds=set()
    for p in pages:
        if p['page_id'] in page_ids:raise StickerWallError('同批背景页重复')
        page_ids.add(p['page_id'])
        fg=p['foreground']
        if fg:
            if fg in foregrounds:raise StickerWallError('同一前景只能占用一张墙')
            foregrounds.add(fg)
        for s in p['sticker_display_info']:
            if s['sticker_id'] in stickers:raise StickerWallError('同一贴纸只能占用一张墙')
            stickers.add(s['sticker_id'])


def merge_pages(extra,incoming,owned,rules=None):
    rules=rules or load_rules()
    if not isinstance(incoming,list):raise StickerWallError('贴纸画册必须是页面列表')
    if not incoming:return pages_from_extra(extra)
    updates=[_page(p,owned,rules) for p in incoming]
    _unique(updates)  # All checks precede taking assets from any old page.
    new_ids={p['page_id'] for p in updates}
    used={s['sticker_id'] for p in updates for s in p['sticker_display_info']}
    fgs={p['foreground'] for p in updates if p['foreground']}
    final=[]
    for old in pages_from_extra(extra):
        if old.get('page_id') in new_ids:continue
        p=deepcopy(old)
        if p.get('foreground',0) in fgs:p['foreground']=0
        kept=[s for s in p.get('sticker_display_info',[]) if s.get('sticker_id') not in used]
        if len(kept)!=len(p.get('sticker_display_info',[])):
            for i,s in enumerate(kept):s['layer']=i+1
        p['sticker_display_info']=kept
        # Retain untouched historical layouts without treating their assets as grants.
        final.append(_page(p,{k:set(rules[k]) for k in ('backgrounds','stickers','foregrounds')},rules))
    final+=updates
    _unique(final)
    return sorted(final,key=lambda p:p['page_id'])


def web_wall_views(extra,game_user,cards,catalog,rules=None):
    rules=rules or load_rules();owned=owned_assets(cards,rules)
    selected=active_page_id(extra,game_user,rules,owned)
    pages={p.get('page_id'):p for p in projected_pages(extra,owned,rules)}
    walls=[]
    for pid in [selected]+[p for p in rules['backgrounds'] if p!=selected]:
        raw=pages.get(pid,{});meta=catalog.get('backgrounds',{}).get(str(pid),{})
        stickers=[]
        for i,s in enumerate(raw.get('sticker_display_info',[])):
            if not isinstance(s,dict) or s.get('sticker_id') not in rules['stickers']:continue
            info=catalog.get('stickers',{}).get(str(s['sticker_id']),{})
            stickers.append(dict(id=s['sticker_id'],name=info.get('name',f"贴纸_{s['sticker_id']}"),
                desc=info.get('desc',''),rare=info.get('rare',5),
                **{k:s.get(k,v) for k,v in [('location_x',0),('location_y',0),('scale',5000),('rotate',0),('layer',i+1)]}))
        stickers.sort(key=lambda s:s['layer'] if type(s['layer']) is int else 0)
        walls.append(dict(page_id=pid,page_name=meta.get('name',f'背景_{pid}'),page_desc=meta.get('desc',''),
            foreground=raw.get('foreground',0),is_active=pid==selected,is_saved=pid in pages,
            is_owned=pid in owned['backgrounds'],total_stickers=len(stickers),stickers=stickers))
    return walls[0],walls


@lru_cache(maxsize=1)
def load_suits():
    raw=json.loads(Path(__file__).with_name('sticker_suit_cfg.json').read_text('utf-8'))
    rules=load_rules();assets=set().union(*(set(rules[k]) for k in ('backgrounds','stickers','foregrounds')))
    suits=raw.get('suits')
    if raw.get('schema')!=1 or not isinstance(suits,dict) or not suits:
        raise StickerWallError('贴纸套装配置无效')
    for key,value in suits.items():
        if not key.isdecimal() or int(key)<=0 or str(int(key))!=key or not isinstance(value,dict):
            raise StickerWallError('贴纸套装ID无效')
        members=value.get('content');rewards=value.get('reward')
        if not isinstance(members,list) or not members or any(type(i) is not int or i not in assets for i in members) or len(set(members))!=len(members):
            raise StickerWallError('贴纸套装成员配置无效')
        if not isinstance(rewards,list) or not rewards or any(not isinstance(r,list) or len(r)!=2 or any(type(n) is not int or not 0<n<=2147483647 for n in r) for r in rewards):
            raise StickerWallError('贴纸套装奖励配置无效')
    return {int(k):v for k,v in suits.items()}


def suit_reward_plan(ids,extra,owned,suits=None):
    suits=load_suits() if suits is None else suits
    if not isinstance(ids,list) or any(type(i) is not int or i not in suits for i in ids):
        raise StickerWallError('贴纸套装ID无效')
    history=extra.get('sticker_suit_rewarded',[])
    if not isinstance(history,list) or any(type(i) is not int or i<=0 for i in history):
        raise StickerWallError('历史贴纸套装领取记录无效，保留原存档')
    rewarded=set(history);new=[];totals={};assets=set().union(*owned.values())
    for sid in dict.fromkeys(ids):
        if sid in rewarded:continue
        if not set(suits[sid]['content'])<=assets:raise StickerWallError('尚未集齐该贴纸套装',406)
        new.append(sid)
        for iid,num in suits[sid]['reward']:
            totals[iid]=totals.get(iid,0)+num
            if totals[iid]>2147483647:raise StickerWallError('套装奖励总量溢出')
    return history+new,[dict(id=i,num=n) for i,n in sorted(totals.items())]
