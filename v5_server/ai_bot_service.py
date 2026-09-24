# -*- coding: utf-8 -*-
"""
ai_bot_service.py — AI 修正者对话中枢与多模型 LLM 调用引擎

核心能力:
- 支持 OpenAI / DeepSeek / OpenCode / Gemini / Anthropic / Ollama 协议适配器；
- 具备严格的敏感密钥脱敏与异常清洗机制，密钥永不外泄；
- 支持同步对话直通 (chat_sync) 与后台非阻塞线程池异步并发 (submit_ai_chat_task)；
- 具备连通性诊断探针 (test_llm_connection)；
- 具备高度契合《深空之眼》原版人设的离线与异常兜底降级库。
"""
import json
import urllib.request
import urllib.error
import threading
from concurrent.futures import ThreadPoolExecutor
import time
import os
import sys
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_bot_config
from ai_context_builder import get_context_builder
from ai_history_manager import get_history_manager


_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="AIBotWorker")
_chat_locks = {}
_chat_locks_guard = threading.Lock()


def _get_chat_lock(uid, char_id):
    key = (int(uid), int(char_id))
    with _chat_locks_guard:
        lock = _chat_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _chat_locks[key] = lock
        return lock


def _sanitize_error(error_str: str, api_key: str = "") -> str:
    """清洗异常信息，杜绝 API Key 或敏感 Token 泄露在错误提示中。"""
    if not error_str:
        return ""
    if api_key and len(api_key) > 5 and api_key in error_str:
        error_str = error_str.replace(api_key, "***")
    # 正则清洗任何 sk- 开头的长密钥
    error_str = re.sub(r"sk-[a-zA-Z0-9_\-]{8,}", "sk-***", error_str)
    # 正则清洗 URL query 中的 key=xxx
    error_str = re.sub(r"key=[a-zA-Z0-9_\-]+", "key=***", error_str)
    return error_str


def _call_openai_compatible(base_url, api_key, model, system_prompt, messages, temperature=0.7, max_tokens=1000, timeout_seconds=30):
    """调用 OpenAI 兼容端点（包括 DeepSeek, OpenAI, OpenCode, Gemini-OpenAI, Ollama 等）。"""
    url = f"{base_url.rstrip('/')}/chat/completions"

    formatted_msgs = []
    if system_prompt:
        formatted_msgs.append({"role": "system", "content": system_prompt})
    for m in messages:
        formatted_msgs.append({"role": m["role"], "content": m["content"]})

    payload = {
        "model": model,
        "messages": formatted_msgs,
        "temperature": temperature,
        "max_tokens": max_tokens
    }

    req_data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}" if api_key else "",
        "User-Agent": "AetherGazer-AIBot/2.0"
    }

    last_err = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))
                choices = res_json.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "").strip()
            return ""
        except Exception as e:
            last_err = e
            time.sleep(0.5)

    if last_err:
        clean_msg = _sanitize_error(str(last_err), api_key)
        raise RuntimeError(f"OpenAI 接口调用异常: {clean_msg}")
    return ""


def _call_anthropic(base_url, api_key, model, system_prompt, messages, temperature=0.7, max_tokens=1000, timeout_seconds=30):
    """调用 Anthropic Claude 原生端点。"""
    url = f"{base_url.rstrip('/')}/messages"

    formatted_msgs = []
    for m in messages:
        formatted_msgs.append({"role": m["role"], "content": m["content"]})

    payload = {
        "model": model,
        "system": system_prompt,
        "messages": formatted_msgs,
        "temperature": temperature,
        "max_tokens": max_tokens
    }

    req_data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01"
    }

    try:
        req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
            content_list = res_json.get("content", [])
            if content_list and content_list[0].get("text"):
                return content_list[0]["text"].strip()
    except Exception as e:
        clean_msg = _sanitize_error(str(e), api_key)
        raise RuntimeError(f"Claude 接口调用异常: {clean_msg}")
    return ""


def _call_gemini_native(api_key, model, system_prompt, messages, temperature=0.7, max_tokens=1000, timeout_seconds=30):
    """调用 Google Gemini 原生 v1beta generateContent 端点（通过请求头传输 API Key，杜绝 URL 泄露）。"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    contents = []
    for m in messages:
        role = "user" if m["role"] == "user" else "model"
        contents.append({
            "role": role,
            "parts": [{"text": m["content"]}]
        })

    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens
        }
    }
    if system_prompt:
        payload["systemInstruction"] = {
            "parts": [{"text": system_prompt}]
        }

    req_data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key
    }

    try:
        req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
            candidates = res_json.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts and parts[0].get("text"):
                    return parts[0]["text"].strip()
    except Exception as e:
        clean_msg = _sanitize_error(str(e), api_key)
        raise RuntimeError(f"Gemini 接口调用异常: {clean_msg}")
    return ""


def call_llm(system_prompt, messages, cfg=None):
    """统一入口：根据当前配置安全调用指定 LLM Provider。"""
    if cfg is None:
        cfg = ai_bot_config.get_bot_config()

    provider_name = cfg.get("provider", "opencode")
    p_info = cfg.get("providers", {}).get(provider_name, {})

    p_type = p_info.get("type", "openai")
    base_url = p_info.get("base_url", "")
    model = p_info.get("model", "")
    api_key = p_info.get("api_key", "")
    temp = cfg.get("temperature", 0.7)
    max_tok = cfg.get("max_tokens", 1000)
    try:
        timeout_seconds = max(1, min(120, int(cfg.get("timeout_seconds", 30))))
    except (TypeError, ValueError):
        timeout_seconds = 30

    if not api_key and provider_name != "ollama":
        raise ValueError(f"Provider [{provider_name}] 未配置有效 API Key！请在控制台设置或写入 v5_server/.env。")

    if p_type == "anthropic":
        return _call_anthropic(base_url, api_key, model, system_prompt, messages, temp, max_tok, timeout_seconds)
    elif p_type == "gemini_native":
        return _call_gemini_native(api_key, model, system_prompt, messages, temp, max_tok, timeout_seconds)
    else:
        # 默认 openai 兼容协议 (涵盖 deepseek, openai, opencode, gemini-openai, ollama 等)
        return _call_openai_compatible(base_url, api_key, model, system_prompt, messages, temp, max_tok, timeout_seconds)


generate_response = call_llm


def get_fallback_reply(character, user_msg):
    """API 离线或调用异常时的角色深度人设降级台词（杜绝脱离角色）。"""
    char_name = character.get("char_name", "修正者") if isinstance(character, dict) else str(character)
    canned = {
        "海拉": "……管理员，周围很安静。刚才迟钝在旁边小声嘀咕，我有点走神了。等我整理完迟钝的衣服再和你细聊吧。",
        "薇儿丹蒂": "管理员！第九部门的网络刚才好像有点小波动呢，消息稍微有些卡顿。不过不要紧，薇儿丹蒂随时待命，今天也一起加油吧！",
        "大国主": "哎呀！兔兔通讯器怎么突然冒烟啦……管理员笨蛋你先等我两分钟，本天才发明家敲它两锤子马上修好！",
        "庚辰": "小友，虚恒云起风生，声息稍阻。静坐片刻，待心绪宁定，再叙今日闲话亦未迟。",
        "陵光": "管理员，通讯偶有微滞，无需介怀。今日神思劳顿否？我煎好的温润安神茶已备在案前，且宽心饮尽。",
        "奥西里斯": "前、前辈！通讯好像有些微的杂音……雏心一直守在通讯器这边的！前辈不用担心，雏心绝不会给前辈添麻烦的！",
        "托尔": "哈哈哈哈！刚才雷鸣太响把耳朵震麻了！老兄你刚才说啥？走，等下切磋完了直接去痛饮一杯！"
    }
    return canned.get(char_name, f"【{char_name}】管理员，通讯信号连接中……稍候再叙。")


def test_llm_connection(provider_name=None, cfg=None) -> dict:
    """连通性诊断探针：测试大模型 API 连通性与往返延迟。"""
    if cfg is None:
        cfg = ai_bot_config.get_bot_config()

    provider = provider_name or cfg.get("provider", "opencode")
    p_info = cfg.get("providers", {}).get(provider, {})
    if not p_info:
        return {
            "success": False,
            "latency_ms": 0,
            "provider": provider,
            "model": "",
            "reply": "",
            "error": f"未找到指定 Provider [{provider}] 的配置"
        }

    api_key = p_info.get("api_key", "")
    if not api_key and provider != "ollama":
        return {
            "success": False,
            "latency_ms": 0,
            "provider": provider,
            "model": p_info.get("model", ""),
            "reply": "",
            "error": f"Provider [{provider}] 未配置 API Key，无法发起连接测试"
        }

    test_cfg = {
        "provider": provider,
        "temperature": 0.3,
        "max_tokens": 50,
        "timeout_seconds": cfg.get("timeout_seconds", 30),
        "providers": {provider: p_info}
    }

    t0 = time.time()
    try:
        reply = call_llm("你是一个连通性测试助手。请直接回复：PONG", [{"role": "user", "content": "PING"}], cfg=test_cfg)
        latency = int((time.time() - t0) * 1000)
        return {
            "success": True,
            "latency_ms": latency,
            "provider": provider,
            "model": p_info.get("model", ""),
            "reply": reply[:100],
            "error": None
        }
    except Exception as e:
        latency = int((time.time() - t0) * 1000)
        sanitized = _sanitize_error(str(e), api_key)
        return {
            "success": False,
            "latency_ms": latency,
            "provider": provider,
            "model": p_info.get("model", ""),
            "reply": "",
            "error": sanitized
        }


def chat_sync(uid, char_id, user_msg, db, cfg=None, log_fn=None) -> dict:
    """串行化同一玩家与角色的完整对话，避免并发请求交错上下文。"""
    with _get_chat_lock(uid, char_id):
        return _chat_sync_locked(uid, char_id, user_msg, db, cfg=cfg, log_fn=log_fn)


def _chat_sync_locked(uid, char_id, user_msg, db, cfg=None, log_fn=None) -> dict:
    """同步直通对话接口（供 GM 控制台与管理接口调用）。

    执行步骤:
    1. 校验并读取角色人设；
    2. 持久化玩家输入至 ai_chat_memory 与 friend_chat；
    3. 提取历史滑动窗口构造 Prompt；
    4. 调用 LLM（失败则执行人设降级兜底）；
    5. 持久化 AI 回复；
    6. 返回全量状态包（含耗时与来源标记）。
    """
    log = log_fn or (lambda *a, **k: None)
    char_info = db.get_ai_character(char_id) if hasattr(db, "get_ai_character") else None
    if not char_info:
        return {
            "success": False,
            "error": f"未找到角色 char_id={char_id}",
            "reply_text": "",
            "is_fallback": True,
            "fallback_reason": "角色不存在",
            "latency_ms": 0,
            "timestamp": int(time.time()),
        }

    cfg = cfg or ai_bot_config.get_bot_config()
    now_ts = int(time.time())

    # 1. 严格防 OOC 校验：未配置专属人格提示词的角色拒绝会话与广播
    persona_prompt = ""
    if hasattr(db, "get_character_persona_prompt"):
        persona_prompt = db.get_character_persona_prompt(uid, char_id) or ""
    elif char_info.get("system_prompt"):
        persona_prompt = char_info.get("system_prompt") or ""

    if not persona_prompt or not persona_prompt.strip():
        # 未配置人设：不记录玩家消息、不请求大模型、不记录回复，零上库杜绝污染
        log(f"[AIBot] 角色 char_id={char_id} 未配置专属人格提示词，已拦截对话并保持静音", "INFO")
        return {
            "success": True,
            "char_id": int(char_id),
            "char_name": char_info.get("char_name", "修正者"),
            "user_msg_id": 0,
            "reply_msg_id": int(time.time() * 1000) % 2147483647,
            "reply_text": "当前角色没有对应的人格提示词",
            "is_fallback": True,
            "fallback_reason": "未配置专属人格提示词",
            "latency_ms": 0,
            "model": "rule_interceptor",
            "provider": "system",
            "timestamp": now_ts,
            "history_source": "none",
            "prompt_context_version": 0,
            "has_persona": False,
        }

    # 2. 记录玩家消息
    user_msg_id = db.save_ai_chat_message(uid, char_id, "user", user_msg, timestamp=now_ts)

    # 3. 读取客户端权威历史，并用 SQLite 补齐面板消息或客户端缺口。
    max_turns = max(1, int(cfg.get("max_history_turns", 10)))
    history_result = get_history_manager().get_history(
        uid,
        char_id,
        limit=max_turns * 2,
        db=db,
    )
    history = history_result["messages"]
    messages = [
        {"role": item["role"], "content": item["content"]}
        for item in history
        if item.get("role") in ("user", "assistant")
    ]

    # 保证当前消息位于最末尾
    if not messages or messages[-1]["role"] != "user" or messages[-1]["content"] != user_msg:
        messages.append({"role": "user", "content": user_msg})

    # 固定规则、人设与动态亲和上下文只存在于本轮内存，不写入任何历史源。
    context_history = history
    if (
        history
        and history[-1].get("role") == "user"
        and history[-1].get("content") == user_msg
        and int(history[-1].get("timestamp", 0) or 0) >= now_ts - 1
    ):
        # “久未联系”应以本次发送前的最后互动计算，不能被刚落库的当前消息覆盖。
        context_history = history[:-1]
    context_result = get_context_builder().build(
        uid,
        char_info,
        db=db,
        history=context_history,
    )
    prompt_parts = []
    if cfg.get("system_prefix"):
        prompt_parts.append(cfg.get("system_prefix").strip())
    if persona_prompt.strip():
        prompt_parts.append(persona_prompt.strip())
    if context_result.get("text"):
        prompt_parts.append(context_result.get("text").strip())
    system_prompt = "\n\n".join(prompt_parts)

    t0 = time.time()
    is_fallback = False
    fallback_reason = ""
    reply_text = ""

    try:
        reply_text = call_llm(system_prompt, messages, cfg)
        if not reply_text:
            is_fallback = True
            fallback_reason = "大模型返回内容为空"
            reply_text = get_fallback_reply(char_info, user_msg)
    except Exception as e:
        is_fallback = True
        cur_p = cfg.get("provider", "")
        api_key = cfg.get("providers", {}).get(cur_p, {}).get("api_key", "")
        fallback_reason = _sanitize_error(str(e), api_key)
        log(f"[AIBot] LLM 调用失败: {fallback_reason}，启用人设降级回复", "WARN")
        reply_text = get_fallback_reply(char_info, user_msg)

    latency_ms = int((time.time() - t0) * 1000)

    # 3. 记录 AI 回复
    reply_ts = int(time.time())
    reply_msg_id = db.save_ai_chat_message(uid, char_id, "assistant", reply_text, timestamp=reply_ts)

    cur_provider = cfg.get("provider", "opencode")
    cur_model = cfg.get("providers", {}).get(cur_provider, {}).get("model", "")

    return {
        "success": True,
        "has_persona": True,
        "char_id": int(char_id),
        "char_name": char_info.get("char_name", "修正者"),
        "user_msg_id": user_msg_id,
        "reply_msg_id": reply_msg_id,
        "reply_text": reply_text,
        "is_fallback": is_fallback,
        "fallback_reason": fallback_reason,
        "latency_ms": latency_ms,
        "model": cur_model,
        "provider": cur_provider,
        "timestamp": reply_ts,
        "history_source": history_result.get("source", "sqlite"),
        "prompt_context_version": context_result.get("version", 0),
    }


def submit_ai_chat_task(uid, char_id, user_msg, db, on_reply_callback, log_fn=None):
    """游戏局内异步私聊提交入口（由网络层中间件调用，非阻塞线程池驱动）。"""
    def _worker():
        try:
            res = chat_sync(uid, char_id, user_msg, db, log_fn=log_fn)
            if res.get("success") and on_reply_callback:
                char_info = db.get_ai_character(char_id)
                on_reply_callback(uid, char_id, res["reply_text"], res["reply_msg_id"], char_info)
        except Exception as e:
            if log_fn:
                log_fn(f"[AIBot] 异步线程池任务异常: {e}", "ERROR")

    _executor.submit(_worker)
