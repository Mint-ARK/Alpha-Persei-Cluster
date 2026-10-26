# -*- coding: utf-8 -*-
"""公开榜单与模型目录聚合；来源、缓存时间和刷新结果分别记录。"""
import csv
import io
import math
import os
import json
import re
import ssl
import shutil
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_leaderboards_cache.json")
_REFRESH_LOCK = threading.Lock()


def _make_ssl_context():
    """构造安全且容错度高的 SSL 上下文，防御 Cloudflare 瞬时 EOF 异常。"""
    return ssl.create_default_context()


def _fetch_url(url: str, timeout: int = 15, retries: int = 2, headers: dict = None) -> bytes:
    """通用稳健网络拉取器，带重试与自定义 SSL 上下文。"""
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain,*/*;q=0.8"
    }
    if headers:
        req_headers.update(headers)
    last_err = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, context=_make_ssl_context(), timeout=timeout) as resp:
                body = resp.read(8 * 1024 * 1024 + 1)
                if len(body) > 8 * 1024 * 1024:
                    raise ValueError("榜单响应超过8MB")
                return body
        except Exception as e:
            last_err = e
    if last_err:
        raise last_err
    return b""


# 元数据独立于缓存提供，首次安装也有完整的榜单入口。
_SOURCE_SPECS = [
    ("eqbench_creative", "EQ-Bench 创意写作", "BookOpen", "https://eqbench.com/creative_writing.html", "Elo", "EQ-Bench 创意写作评测，按源评分排序。"),
    ("eqbench_longform", "EQ-Bench 长篇叙事", "Scroll", "https://eqbench.com/creative_writing_longform.html", "综合得分", "EQ-Bench 长篇写作评测，按源评分排序。"),
    ("lmsys_arena", "Arena 文本总榜", "Swords", "https://arena.ai/leaderboard/text", "Arena 分数", "Arena 文本综合对战排名；经 Jina Reader 读取官方页面。"),
    ("openrouter_rp", "OpenRouter 模型目录", "Drama", "https://openrouter.ai/models", "上架日期", "按上架时间倒序列出模型；此目录没有能力或热度评分。"),
    ("artificial_analysis", "Artificial Analysis", "Zap", "https://artificialanalysis.ai/leaderboards/models", "暂未接入", "尚未接入实时数据，暂不展示来源无法核实的旧分数。"),
    ("composite_average", "综合平均天梯（娱乐）", "Sparkles", "", "平均名次", "仅汇总 EQ-Bench 与 Arena 的名次；未上榜按第46名折算，仅供娱乐。"),
]


def get_leaderboard_data() -> dict:
    """只读缓存并规范旧格式，不把本次读取时间当成数据更新时间。"""
    cached = {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            candidate = json.load(f)
        if isinstance(candidate, dict) and isinstance(candidate.get("sources"), list):
            cached = candidate
    except (OSError, ValueError):
        pass
    previous = {s.get("id"): s for s in cached.get("sources", []) if isinstance(s, dict)}
    sources = []
    for sid, name, icon, url, metric, description in _SOURCE_SPECS:
        old = previous.get(sid, {})
        items = old.get("items", [])
        if not isinstance(items, list):
            items = []
        # 旧OpenRouter是人工热度分，不能当作新目录数据继续展示。
        if sid in ("artificial_analysis", "composite_average") or (sid == "openrouter_rp" and cached.get("schema_version") != 2):
            items = []
        source = dict(id=sid, name=name, icon=icon, url=url, metric_name=metric,
                      description=description, columns=["排名", "模型", "厂商", metric, "详情", "操作"],
                      items=items, updated_at=old.get("updated_at", cached.get("updated_at", "")) if items else "",
                      status="cached" if items else "empty", error="")
        if sid == "artificial_analysis":
            source["status"] = "unavailable"
        sources.append(source)
    composite = sources[-1]
    composite["items"] = _compute_composite_average(sources, max_items=55)
    composite["status"] = "derived" if composite["items"] else "empty"
    composite["updated_at"] = cached.get("updated_at", "") if composite["items"] else ""
    return {"schema_version": 2, "updated_at": cached.get("updated_at", ""), "sources": sources}


def _infer_provider(raw_name: str, vendor: str = "") -> str:
    """推断模型对应的主要厂商标识（openai/anthropic/gemini/deepseek/glm/ollama/custom）。"""
    v_low = (vendor or "").lower()
    if "anthropic" in v_low:
        return "anthropic"
    if "google" in v_low:
        return "gemini"
    if "openai" in v_low:
        return "openai"
    if "deepseek" in v_low:
        return "deepseek"
    if "z.ai" in v_low or "zhipu" in v_low or "glm" in v_low:
        return "glm"
    if "meta" in v_low or "facebook" in v_low:
        return "ollama"
    if "moonshot" in v_low:
        return "custom"
    if "alibaba" in v_low or "qwen" in v_low:
        return "ollama"

    low = (raw_name or "").lower()
    if "gpt" in low or "o1" in low or "o3" in low or "o4" in low or "openai" in low:
        return "openai"
    if "claude" in low or "anthropic" in low:
        return "anthropic"
    if "gemini" in low or "google" in low:
        return "gemini"
    if "deepseek" in low:
        return "deepseek"
    if "glm" in low or "ox-alpha" in low or "zai-org" in low:
        return "glm"
    if any(k in low for k in ["qwen", "llama", "mistral", "nemotron", "nvidia", "meta-models", "muse", "yi", "phi"]):
        return "ollama"
    return "custom"


def _parse_csv_by_headers(csv_text: str, source_type: str = "creative", max_items: int = 50) -> list:
    """基于 CSV 表头动态解析 EQ-Bench 榜单数据，并按评分严格降序重排，消灭追加末尾导致的老数据截断缺陷。"""
    rows = list(csv.reader(io.StringIO(csv_text.strip())))
    if len(rows) <= 1:
        return []

    headers = [h.strip().lower().lstrip('\ufeff') for h in rows[0]]
    header_map = {name: idx for idx, name in enumerate(headers)}

    raw_items = []
    for row in rows[1:]:
        parts = [p.strip() for p in row]
        if not parts or not parts[0]:
            continue

        raw_name = parts[0].lstrip("*")
        provider = _infer_provider(raw_name)

        def get_val(key: str, default: str = "") -> str:
            idx = header_map.get(key)
            if idx is not None and idx < len(parts):
                return parts[idx]
            return default

        score_val = 0.0
        details = {}
        sub_score_val = ""

        if source_type == "creative":
            try:
                score_val = float(get_val("elo_score", parts[1] if len(parts) > 1 else "0"))
            except (ValueError, TypeError):
                continue
            sub_score_val = get_val("creative_writing_score", parts[2] if len(parts) > 2 else str(score_val))
            details = {
                "length": get_val("avg_length", parts[3] if len(parts) > 3 else ""),
                "vocab": get_val("vocab_complexity", parts[4] if len(parts) > 4 else ""),
                "slop": get_val("slop_score", parts[5] if len(parts) > 5 else ""),
                "repetition": get_val("repetition_score", parts[6] if len(parts) > 6 else "")
            }
        elif source_type == "longform":
            try:
                score_val = float(get_val("overall_score_100", parts[1] if len(parts) > 1 else "0"))
            except (ValueError, TypeError):
                continue
            sub_score_val = f"{score_val} 分"
            details = {
                "length": get_val("avg_chapter_length", parts[2] if len(parts) > 2 else ""),
                "vocab": get_val("vocab_complexity", parts[3] if len(parts) > 3 else ""),
                "slop": get_val("slop_score", parts[4] if len(parts) > 4 else ""),
                "repetition": get_val("repetition_score", parts[5] if len(parts) > 5 else "")
            }

        if not math.isfinite(score_val):
            continue
        raw_items.append({
            "model": raw_name,
            "provider": provider,
            "score": score_val,
            "sub_score": sub_score_val,
            "metric_val": str(score_val),
            "details": details
        })

    # 关键：按客观评分降序重排，确保最新追加在 CSV 末尾的前沿模型（如 claude-opus-5, gpt-6-astra）精准霸榜
    raw_items.sort(key=lambda x: x["score"], reverse=True)

    items = []
    for rank, it in enumerate(raw_items[:max_items], 1):
        raw_name = it["model"]
        tags = []
        if rank <= 3:
            tags.append(f"Top {rank}")

        low = raw_name.lower()
        if raw_name == "gpt-5.6":
            tags.append("指向 sol")
        if "ox-alpha" in low:
            tags.append("智谱GLM")
            tags.append("更名 GLM 5.3 Flash")
            tags.append("多模态支持")
        if "flash" in low:
            tags.append("极速响应")
        if "fable" in low or "opus" in low or "sonnet" in low:
            tags.append("叙事大师")
        if "k3" in low or "k2" in low:
            tags.append("长文本")

        it["rank"] = rank
        it["tags"] = tags
        items.append(it)

    return items


def _fetch_eqbench_live(url: str, var_name: str, source_type: str = "creative", max_items: int = 45) -> list:
    """从 EQ-Bench 在线 JS 脚本解析最新的 CSV 数据并动态绑定表头并重排。"""
    raw_bytes = _fetch_url(url, timeout=15)
    text = raw_bytes.decode("utf-8", errors="ignore")

    pattern = rf'{var_name}\s*=\s*`([^`]+)`'
    match = re.search(pattern, text)
    if not match:
        return []

    return _parse_csv_by_headers(match.group(1), source_type=source_type, max_items=max_items)


def _fetch_lmsys_arena_live(max_items: int = 40) -> list:
    """从 Arena 官方文本总榜读取分数及页面实际提供的指标。"""
    # 策略 1：优先从 Jina Reader 渲染的官方真实天梯提取（支持完整 40+ 款前沿模型）
    try:
        url_jina = "https://r.jina.ai/https://arena.ai/leaderboard/text"
        jina_headers = {
            "Accept": "text/plain",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }
        raw_bytes = _fetch_url(url_jina, timeout=15, headers=jina_headers)
        text = raw_bytes.decode("utf-8", errors="ignore")
        lines = [l.strip() for l in text.splitlines()]

        parsed_models = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.isdigit() and 1 <= int(line) <= 150:
                rank_raw = int(line)
                m_name = None
                vendor = ""
                score_val = None
                ci_str = ""
                votes_str = ""

                for j in range(i + 1, min(i + 15, len(lines))):
                    l = lines[j]
                    if "·" in l and any(k in l for k in ["Proprietary", "Open", "Apache", "MIT", "Custom"]):
                        vendor = l.split("·")[0].strip()
                        m_name = lines[j - 1].strip()
                    elif l.isdigit() and 1000 <= int(l) <= 1700 and score_val is None:
                        score_val = float(l)
                    elif l.startswith("±"):
                        ci_str = l
                    elif "\t" in l:
                        parts = l.split("\t")
                        if len(parts) >= 1 and re.sub(r'[\d,]', '', parts[0].strip()) == '':
                            votes_str = parts[0].strip()

                if m_name and score_val is not None:
                    parsed_models.append({
                        "model": m_name,
                        "vendor": vendor,
                        "score": score_val,
                        "ci": ci_str,
                        "votes": votes_str
                    })
                    i += 8
                    continue
            i += 1

        if parsed_models:
            items = []
            for rank, m in enumerate(parsed_models[:max_items], 1):
                model_name = m["model"]
                vendor = m["vendor"]
                provider = _infer_provider(model_name, vendor)
                score_val = m["score"]
                ci_val = m["ci"]
                votes_val = m["votes"]

                tags = []
                if rank <= 3:
                    tags.append(f"Top {rank}")
                if votes_val:
                    tags.append("实测投票")
                low = model_name.lower()
                if "high" in low or "max" in low:
                    tags.append("旗舰推理")
                if "flash" in low:
                    tags.append("极速响应")
                if "fable" in low or "opus" in low:
                    tags.append("前沿拟真")

                items.append({
                    "rank": rank,
                    "model": model_name,
                    "provider": provider,
                    "score": score_val,
                    "sub_score": ci_val,
                    "metric_val": str(int(score_val) if score_val.is_integer() else score_val),
                    "details": {
                        "ci": ci_val,
                        "votes": votes_val,
                        "vendor": vendor,
                        "license": "proprietary"
                    },
                    "tags": tags
                })
            return items
    except Exception as exc:
        raise RuntimeError(f"Arena 官方页面读取失败: {exc}") from exc
    raise ValueError("Arena 页面未解析出有效排名")


def _fetch_openrouter_live(max_items: int = 40) -> list:
    """从 OpenRouter 官方模型端点提取前沿活跃模型并格式化。"""
    url = "https://openrouter.ai/api/v1/models"
    raw_bytes = _fetch_url(url, timeout=15)
    res_json = json.loads(raw_bytes.decode("utf-8", errors="ignore"))

    data_list = res_json.get("data", [])
    if not data_list:
        return []

    sorted_models = sorted(
        data_list,
        key=lambda x: (
            int(x.get("created", 0)),
            int(x.get("context_length", 0))
        ),
        reverse=True
    )

    items = []
    for rank, m in enumerate(sorted_models[:max_items], 1):
        m_id = m.get("id", "").lstrip("~")
        name = m.get("name", m_id)
        ctx = m.get("context_length", 0)
        provider = _infer_provider(m_id)

        tags = []
        if ctx >= 1000000:
            tags.append("1M超长上下文")
        elif ctx >= 200000:
            tags.append("200K长文本")

        items.append({
            "rank": rank,
            "model": m_id,
            "provider": provider,
            "score": None,
            "sub_score": f"{ctx // 1000}k ctx" if ctx else "",
            "metric_val": datetime.fromtimestamp(int(m.get("created", 0))).strftime("%Y-%m-%d") if m.get("created") else "未提供",
            "details": {
                "context": f"{ctx:,} tokens" if ctx else "自适应",
                "name": name,
                "created": m.get("created", 0)
            },
            "tags": tags
        })
    return items


def _build_aa_discrete_boards(aa_base_items: list, max_items: int = 40) -> tuple:
    """从 Artificial Analysis 质速基准中，生成分立的「智能质量榜」和「极速生成榜」。"""
    if not aa_base_items:
        return None, None

    # 1. AA 智能质量榜 (按质量指数降序)
    quality_sorted = sorted(aa_base_items, key=lambda x: float(x.get("score", 0)), reverse=True)
    quality_items = []
    for rank, it in enumerate(quality_sorted[:max_items], 1):
        clean_tags = [t for t in it.get("tags", []) if not t.startswith("Top")]
        tags = [f"Top {rank}"] + clean_tags if rank <= 3 else clean_tags
        score_num = float(it.get("score", 0))
        quality_items.append({
            "rank": rank,
            "model": it["model"],
            "provider": it.get("provider", "custom"),
            "score": score_num,
            "sub_score": f"{score_num:.1f} 质量分",
            "metric_val": f"{score_num:.1f} 分",
            "details": it.get("details", {}),
            "tags": tags[:4]
        })

    quality_source = {
        "id": "aa_quality",
        "name": "AA 智能质量榜",
        "icon": "Brain",
        "url": "https://artificialanalysis.ai/leaderboards/models",
        "metric_name": "综合质量指数 (Quality Index)",
        "description": "由 Artificial Analysis 独立评估的大模型综合智能与推理质量指数，涵盖 MMLU-Pro、GPQA、HumanEval 等多维基准。",
        "columns": [
            "排名",
            "模型名称",
            "厂商",
            "综合质量指数",
            "首字延迟 (TTFT)",
            "输出速度 (TPS)",
            "百万Token价格",
            "操作"
        ],
        "items": quality_items
    }

    # 2. AA 极速生成榜 (按 TPS 生成吞吐速率降序)
    def parse_tps(it):
        tps_str = str(it.get("details", {}).get("tps", "0"))
        m = re.search(r'([\d\.]+)', tps_str)
        return float(m.group(1)) if m else 0.0

    speed_sorted = sorted(aa_base_items, key=parse_tps, reverse=True)
    speed_items = []
    for rank, it in enumerate(speed_sorted[:max_items], 1):
        tps_val = parse_tps(it)
        clean_tags = [t for t in it.get("tags", []) if not t.startswith("Top")]
        tags = [f"Top {rank}"] if rank <= 3 else []
        if tps_val >= 140:
            tags.append("百字极速")
        elif tps_val >= 100:
            tags.append("高吞吐")
        tags.extend(clean_tags)

        speed_items.append({
            "rank": rank,
            "model": it["model"],
            "provider": it.get("provider", "custom"),
            "score": round(tps_val, 1),
            "sub_score": f"{tps_val:.1f} t/s",
            "metric_val": f"{tps_val:.1f} t/s",
            "details": it.get("details", {}),
            "tags": tags[:4]
        })

    speed_source = {
        "id": "aa_speed",
        "name": "AA 极速生成榜",
        "icon": "Gauge",
        "url": "https://artificialanalysis.ai/leaderboards/models",
        "metric_name": "生成吞吐速度 (TPS)",
        "description": "由 Artificial Analysis 实测的大语言模型 API 实际吐字生成速率（Tokens/s），精准反映流畅对话与低延迟推理体验。",
        "columns": [
            "排名",
            "模型名称",
            "厂商",
            "输出速度 (TPS)",
            "首字延迟 (TTFT)",
            "综合质量指数",
            "百万Token价格",
            "操作"
        ],
        "items": speed_items
    }

    return quality_source, speed_source


def _compute_composite_average(sources: list, max_items: int = 60) -> list:
    """根据所有有效客观榜单（剔除自嗨榜自身与停更的 AlpacaEval），动态归一化计算「综合平均天梯 (娱乐自嗨榜)」。"""
    benchmark_sources = [
        s for s in sources
        if s.get("id") in ("eqbench_creative", "eqbench_longform", "lmsys_arena") and s.get("items")
    ]
    total_benchmarks = len(benchmark_sources)
    if total_benchmarks == 0:
        return []

    model_stats = {}
    for src in benchmark_sources:
        for item in src.get("items", []):
            m_name = item.get("model")
            if not m_name:
                continue
            if m_name not in model_stats:
                model_stats[m_name] = {
                    "provider": item.get("provider", "custom"),
                    "ranks": [],
                    "tags": set(item.get("tags", []))
                }
            model_stats[m_name]["ranks"].append(item.get("rank", 50))
            for t in item.get("tags", []):
                model_stats[m_name]["tags"].add(t)

    penalty_rank = 46.0
    composite_list = []
    for m_name, stat in model_stats.items():
        ranks = stat["ranks"]
        appearances = len(ranks)
        in_list_avg = sum(ranks) / appearances
        missing_count = max(0, total_benchmarks - appearances)
        avg_rank = (sum(ranks) + missing_count * penalty_rank) / total_benchmarks

        composite_list.append({
            "model": m_name,
            "provider": stat["provider"],
            "avg_rank": avg_rank,
            "in_list_avg": in_list_avg,
            "appearances": appearances,
            "tags": stat["tags"]
        })

    composite_list.sort(key=lambda x: (x["avg_rank"], -x["appearances"]))

    results = []
    for rank, it in enumerate(composite_list[:max_items], 1):
        m_name = it["model"]
        avg_r = it["avg_rank"]
        in_avg = it["in_list_avg"]
        apps = it["appearances"]
        norm_score = round(max(50.0, 99.0 - (avg_r - 1.0) * 1.35), 1)

        tags = []
        if rank <= 3:
            tags.append(f"Top {rank}")
        if apps == total_benchmarks:
            tags.append("全榜霸榜")
        elif apps >= total_benchmarks - 1:
            tags.append("高在榜率")
        elif apps >= 3:
            tags.append("多榜收录")

        if m_name == "gpt-5.6":
            tags.append("指向 sol")
        if "ox-alpha" in m_name.lower():
            tags.append("更名 GLM 5.3 Flash")

        results.append({
            "rank": rank,
            "model": m_name,
            "provider": it["provider"],
            "score": norm_score,
            "sub_score": f"均名 #{avg_r:.1f}",
            "metric_val": f"#{avg_r:.1f}",
            "details": {
                "in_list_avg": f"#{in_avg:.1f}",
                "appearances": f"{apps}/{total_benchmarks}",
                "presence_pct": f"{int(apps / total_benchmarks * 100)}%"
            },
            "tags": list(tags[:4])
        })
    return results


def _save_cache(data: dict) -> None:
    """写前备份已有缓存，写入临时文件后原子替换。"""
    if os.path.exists(CACHE_FILE):
        archive = os.path.join(os.path.dirname(CACHE_FILE), "archive", "ai_leaderboards_cache")
        os.makedirs(archive, exist_ok=True)
        shutil.copy2(CACHE_FILE, os.path.join(archive, f"{time.time_ns()}.json"))
    temporary = CACHE_FILE + f".{time.time_ns()}.tmp"
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, allow_nan=False)
    os.replace(temporary, CACHE_FILE)


def refresh_leaderboard_data() -> dict:
    """四个独立来源并发、每源最多两次；失败保留旧数据和旧更新时间。"""
    current = get_leaderboard_data()
    if not _REFRESH_LOCK.acquire(blocking=False):
        return dict(success=False, updated_sources=0, errors=["榜单正在刷新，请稍后再试"], data=current)
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        jobs = {
            "eqbench_creative": lambda: _fetch_eqbench_live("https://eqbench.com/creative_writing.js", "leaderboardDataCreativeWritingV3", "creative"),
            "eqbench_longform": lambda: _fetch_eqbench_live("https://eqbench.com/creative_writing_longform.js", "leaderboardDataLongformV3", "longform"),
            "lmsys_arena": _fetch_lmsys_arena_live,
            "openrouter_rp": _fetch_openrouter_live,
        }
        errors = []
        updated = 0
        by_id = {s["id"]: s for s in current["sources"]}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {sid: pool.submit(fetch) for sid, fetch in jobs.items()}
            for sid, future in futures.items():
                source = by_id[sid]
                try:
                    items = future.result()
                    if not items:
                        raise ValueError("源未返回有效条目")
                    source.update(items=items, updated_at=now, status="updated", error="")
                    updated += 1
                except Exception as exc:
                    source["status"] = "cached" if source["items"] else "error"
                    source["error"] = str(exc)
                    errors.append(f"{source['name']}: {exc}")
        if updated:
            current["updated_at"] = now
            composite = by_id["composite_average"]
            composite.update(items=_compute_composite_average(current["sources"], max_items=55),
                             status="derived", updated_at=now)
            try:
                _save_cache(current)
            except Exception as exc:
                errors.append(f"缓存保存失败: {exc}")
        return dict(success=updated > 0, updated_sources=updated, errors=errors, data=current)
    finally:
        _REFRESH_LOCK.release()
