"""Daily login chat greetings, separate from calendar gift mail."""
import concurrent.futures
import datetime
import json
import logging
import threading
import time

import ai_bot_config
import ai_bot_service
from ai_calendar import get_calendar_events
from ai_context_builder import get_context_builder
from ai_history_manager import get_history_manager
from ai_translator import AITranslator
from event_bus import Events, bus

logger = logging.getLogger(__name__)
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix='AIHello')
_pending = set()
_pending_lock = threading.Lock()


def _character(db, cid):
    return db.get_ai_character(cid) or db.get_ai_character_by_hero_id(cid)


def greeting_plan(db, uid, today):
    """Respect existing all/holiday/birthday/none preferences; one canonical role slot."""
    extra = json.loads(db.get('users', uid)['extra'] or '{}')
    preferences = {}
    for raw_id, mode in extra.get('ai_greeting_settings', {}).items():
        character = _character(db, int(raw_id))
        if character:
            preferences.setdefault(int(character['char_id']), []).append(mode)
    translator = AITranslator(db=db)
    events = [{'type': 'login', 'name': '今日首次登录'}]
    events += [event for event in get_calendar_events(today, uid=uid, db=db)['events']
               if event['type'] in ('user_birthday', 'holiday', 'solar_term')]
    plan = {}
    for event in events:
        for cid in translator.resolve_candidates(uid, db, event):
            character = _character(db, cid)
            if not character or not character.get('is_active'):
                continue
            canonical = int(character['char_id'])
            modes = preferences.get(canonical, [])
            if 'none' in modes:
                continue
            # Explicit preference aliases are canonicalized before dispatch.
            allowed = (not modes or 'all' in modes
                       or ('birthday' in modes and event['type'] == 'user_birthday')
                       or ('holiday' in modes and event['type'] in ('holiday', 'solar_term')))
            if allowed and db.get_ai_proactive_persona(uid, canonical):
                item = dict(type=event['type'], name=event['name'])
                if item not in plan.setdefault(canonical, []):
                    plan[canonical].append(item)
    return plan


def _deliver(db, uid, cid, day, send_message):
    state = db.get_ai_proactive_greeting(uid, cid, day)
    if not state or state['status'] != 'ready':
        return False
    message = db.get_ai_proactive_message(uid, state)
    if not message:
        db.set_ai_proactive_greeting_status(uid, cid, day, 'cancelled')
        return False
    if send_message(uid, cid, state['message_id'], message, db):
        db.set_ai_proactive_greeting_status(uid, cid, day, 'sent')
        return True
    return False


def _greet_character(db, uid, cid, events, today, send_message):
    day = today.isoformat()
    state = db.get_ai_proactive_greeting(uid, cid, day)
    if state:
        return _deliver(db, uid, cid, day, send_message)
    character = db.get_ai_character(cid)
    persona = db.get_ai_proactive_persona(uid, cid)
    clear_ts = db.get_ai_chat_clear_ts(uid, cid)
    cfg = ai_bot_config.resolve_character_config(db, uid, cid)
    provider = cfg.get('provider', '')
    if not cfg.get('providers', {}).get(provider, {}).get('api_key') and provider != 'ollama':
        return False  # No credentials: no request, no consumed daily budget.
    if not db.reserve_ai_proactive_greeting(uid, cid, day, events):
        return False
    try:
        history = get_history_manager().get_history(
            uid, cid, limit=max(1, int(cfg.get('max_history_turns', 10))) * 2, db=db)['messages']
        messages = [{'role': row['role'], 'content': row['content']} for row in history
                    if row.get('role') in ('user', 'assistant')]
        now = datetime.datetime.now().astimezone()
        # Explicit simulated dates are only used by isolated probes; live uses this clock's date.
        now = now.replace(year=today.year, month=today.month, day=today.day)
        environment = dict(local_time=now.isoformat(timespec='seconds'),
                           timezone=now.tzname(), utc_offset=now.strftime('%z'),
                           weekday=('星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日')[now.weekday()])
        context = get_context_builder().build(uid, character, db=db, history=history, now=now.replace(tzinfo=None))
        notification = (
            f'【本轮系统事件通知，仅用于生成，不保存为管理员发言】日期：{day}。'
            f'当前环境：{json.dumps(environment, ensure_ascii=False)}。这是本轮服务端实际时钟，问候早晚与日期以此为准。'
            f'事件：{json.dumps(events, ensure_ascii=False)}。'
            '管理员今天首次进入游戏，请你主动发一条简短自然的私聊问候，结合上述生日或节气、节日信息。'
            '若未列出生日或节日，不要自行编造。称呼与关系遵循实际上下文。'
            '只输出角色说的话，不输出标题、事件通知或系统说明；不要声称发放了道具或邮件。')
        system_prompt = '\n\n'.join(part.strip() for part in (
            cfg.get('system_prefix', ''), persona, context.get('text', ''), notification) if part.strip())
        # Some model adapters require a final user turn. It is an ephemeral event task,
        # never passed to chat_sync and never stored as a fabricated player message.
        messages.append({'role': 'user', 'content': '请根据本轮系统事件通知，主动向管理员打个招呼。'})
        reply = ai_bot_service.call_llm(system_prompt, messages, cfg)
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError('Empty proactive greeting')
        if cid not in greeting_plan(db, uid, today):
            db.set_ai_proactive_greeting_status(uid, cid, day, 'cancelled')
            return False
        message_id = db.complete_ai_proactive_greeting(
            uid, cid, day, reply.strip(), persona, clear_ts, int(time.time()))
        if not message_id:
            db.set_ai_proactive_greeting_status(uid, cid, day, 'cancelled')
            return False
    except Exception as exc:
        db.set_ai_proactive_greeting_status(uid, cid, day, 'failed')
        key = cfg.get('providers', {}).get(provider, {}).get('api_key', '')
        logger.warning('主动问候失败 uid=%s cid=%s: %s', uid, cid,
                       ai_bot_service._sanitize_error(str(exc), key))
        return False
    return _deliver(db, uid, cid, day, send_message)


def greet_login(uid, db, today=None, send_message=None):
    """One generation per day/role, with same-message recovery on same-day relogin."""
    today = today or datetime.date.today()
    if send_message is None:
        from server_net import push_ai_proactive_message
        send_message = push_ai_proactive_message
    sent = 0
    for cid, events in greeting_plan(db, uid, today).items():
        # Share the ordinary chat lock so one role does not answer conflicting contexts.
        with ai_bot_service._get_chat_lock(uid, cid):
            sent += bool(_greet_character(db, uid, cid, events, today, send_message))
    return sent


def submit_login_greeting(uid, db):
    """Coalesce repeated login events before queuing; bounded queue, no automatic retry loop."""
    day = datetime.date.today()
    key = (id(db), int(uid))
    with _pending_lock:
        if key in _pending or len(_pending) >= 32:
            return None
        _pending.add(key)

    def worker():
        try:
            if datetime.date.today() == day:
                return greet_login(uid, db, today=day)
            return 0
        except Exception:
            logger.exception('主动问候调度失败 uid=%s', uid)
            return 0
        finally:
            with _pending_lock:
                _pending.discard(key)
    try:
        return _executor.submit(worker)
    except Exception:
        with _pending_lock:
            _pending.discard(key)
        raise


@bus.subscribe(Events.GAME_LOGIN_READY)
def on_login_ready(ctx, uid, **kwargs):
    return submit_login_greeting(uid, ctx.db)
