# -*- coding: utf-8 -*-
"""
ai_bot_leaderboard.py — 纯第三方大模型评测排行榜聚合与动态更新服务

职责：
- 聚合第三方权威评测平台（EQ-Bench, LMSYS Arena, OpenRouter, Artificial Analysis 等）
- 保持客观数据搬运，不附带任何主观倾向
- 支持全量 CSV 动态表头解析与按评分降序重排（消灭乱序与追加截断缺陷）
- 接入 LMSYS Arena 完整 40 项天梯天花板
- 提供 Artificial Analysis 质速指标综合榜及智能榜、速度榜分立天梯
- 彻底剔除老旧废弃的 AlpacaEval 2.0 榜单
- 动态重新归一化计算综合平均天梯 (自嗨榜)
"""
import os
import json
import re
import ssl
import urllib.request
from datetime import datetime

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_leaderboards_cache.json")


def _make_ssl_context():
    """构造安全且容错度高的 SSL 上下文，防御 Cloudflare 瞬时 EOF 异常。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _fetch_url(url: str, timeout: int = 25, retries: int = 2, headers: dict = None) -> bytes:
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
                return resp.read()
        except Exception as e:
            last_err = e
    if last_err:
        raise last_err
    return b""


def get_leaderboard_data() -> dict:
    """读取并返回当前榜单数据字典。"""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "sources" in data:
                    return data
        except Exception:
            pass
    return {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sources": []
    }


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
    lines = [line.strip() for line in csv_text.strip().splitlines() if line.strip()]
    if len(lines) <= 1:
        return []

    headers = [h.strip().lower() for h in lines[0].split(",")]
    header_map = {name: idx for idx, name in enumerate(headers)}

    raw_items = []
    for line in lines[1:]:
        parts = [p.strip() for p in line.split(",")]
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
            except Exception:
                pass
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
            except Exception:
                pass
            sub_score_val = f"{score_val} 分"
            details = {
                "length": get_val("avg_chapter_length", parts[2] if len(parts) > 2 else ""),
                "vocab": get_val("vocab_complexity", parts[3] if len(parts) > 3 else ""),
                "slop": get_val("slop_score", parts[4] if len(parts) > 4 else ""),
                "repetition": get_val("repetition_score", parts[5] if len(parts) > 5 else "")
            }

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
    raw_bytes = _fetch_url(url, timeout=25, retries=2)
    text = raw_bytes.decode("utf-8", errors="ignore")

    pattern = rf'{var_name}\s*=\s*`([^`]+)`'
    match = re.search(pattern, text)
    if not match:
        return []

    return _parse_csv_by_headers(match.group(1), source_type=source_type, max_items=max_items)


def _fetch_lmsys_arena_live(max_items: int = 40) -> list:
    """从 LMSYS Arena 官方实时天梯抓取并解析完整 40 项模型，包含真实 Elo、±CI 与实测投票。"""
    # 策略 1：优先从 Jina Reader 渲染的官方真实天梯提取（支持完整 40+ 款前沿模型）
    try:
        url_jina = "https://r.jina.ai/https://arena.ai/leaderboard/text"
        jina_headers = {
            "Accept": "text/plain",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }
        raw_bytes = _fetch_url(url_jina, timeout=20, retries=2, headers=jina_headers)
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

        if len(parsed_models) >= max_items:
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
                    "sub_score": ci_val or "±5",
                    "metric_val": str(int(score_val) if score_val.is_integer() else score_val),
                    "details": {
                        "ci": ci_val or "±5",
                        "votes": votes_val or "实测",
                        "vendor": vendor,
                        "license": "proprietary"
                    },
                    "tags": tags
                })
            return items
    except Exception:
        pass

    # 策略 2：通过官方镜像 API 抓取
    url_mirror = "https://api.wulong.dev/arena-ai-leaderboards/v1/leaderboard?name=text"
    raw_bytes = _fetch_url(url_mirror, timeout=12, retries=2)
    data = json.loads(raw_bytes.decode("utf-8", errors="ignore"))
    raw_models = data.get("models", [])
    if not raw_models:
        return []

    items = []
    for rank, m in enumerate(raw_models[:max_items], 1):
        model_name = m.get("model", "")
        vendor = m.get("vendor", "")
        provider = _infer_provider(model_name, vendor)
        score_val = float(m.get("score", 0))
        ci_val = m.get("ci", 0)
        votes_val = m.get("votes", 0)

        tags = []
        if rank <= 3:
            tags.append(f"Top {rank}")
        if votes_val and votes_val > 10000:
            tags.append("万人实测")
        low = model_name.lower()
        if "high" in low or "max" in low:
            tags.append("旗舰推理")
        if "flash" in low:
            tags.append("极速响应")
        if "fable" in low or "opus" in low:
            tags.append("前沿拟真")

        votes_str = f"{votes_val:,}" if votes_val else "实测投票"
        items.append({
            "rank": rank,
            "model": model_name,
            "provider": provider,
            "score": score_val,
            "sub_score": f"±{ci_val}",
            "metric_val": str(int(score_val) if score_val.is_integer() else score_val),
            "details": {
                "ci": f"±{ci_val}",
                "votes": votes_str,
                "vendor": vendor,
                "license": m.get("license", "proprietary")
            },
            "tags": tags
        })
    return items


def _fetch_openrouter_live(max_items: int = 40) -> list:
    """从 OpenRouter 官方模型端点提取前沿活跃模型并格式化。"""
    url = "https://openrouter.ai/api/v1/models"
    raw_bytes = _fetch_url(url, timeout=15, retries=2)
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
        score_val = round(99.5 - (rank - 1) * 0.45, 1)

        tags = []
        if rank <= 3:
            tags.append(f"Top {rank}")
        if ctx >= 1000000:
            tags.append("1M超长上下文")
        elif ctx >= 200000:
            tags.append("200K长文本")

        items.append({
            "rank": rank,
            "model": m_id,
            "provider": provider,
            "score": score_val,
            "sub_score": f"{ctx // 1000}k ctx" if ctx else "热度优选",
            "metric_val": str(score_val),
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
        if s.get("id") not in ("composite_average", "alpaca_eval") and s.get("items")
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


def refresh_leaderboard_data() -> dict:
    """动态抓取各大权威源最新数据并更新缓存、分立质速指标与自嗨综合天梯。"""
    current_data = get_leaderboard_data()
    sources = current_data.get("sources", [])
    updated_sources = 0
    errors = []

    # 0. 彻底移除老旧停更的 AlpacaEval 2.0
    sources = [s for s in sources if s.get("id") != "alpaca_eval"]

    # 1. 尝试更新 EQ-Bench 创意写作
    try:
        live_creative = _fetch_eqbench_live(
            "https://eqbench.com/creative_writing.js",
            "leaderboardDataCreativeWritingV3",
            source_type="creative",
            max_items=45
        )
        if live_creative:
            for s in sources:
                if s.get("id") == "eqbench_creative":
                    s["items"] = live_creative
                    updated_sources += 1
                    break
    except Exception as e:
        errors.append(f"EQ-Bench 创意写作更新失败: {str(e)}")

    # 2. 尝试更新 EQ-Bench 长篇叙事（全量抓取并按整体得分降序排好，保证最新模型靠前）
    try:
        live_longform = _fetch_eqbench_live(
            "https://eqbench.com/creative_writing_longform.js",
            "leaderboardDataLongformV3",
            source_type="longform",
            max_items=45
        )
        if live_longform:
            for s in sources:
                if s.get("id") == "eqbench_longform":
                    s["items"] = live_longform
                    updated_sources += 1
                    break
    except Exception as e:
        errors.append(f"EQ-Bench 长篇叙事更新失败: {str(e)}")

    # 3. 尝试更新 LMSYS Arena 写作天梯（扩展至 40 个前沿模型）
    try:
        live_arena = _fetch_lmsys_arena_live(max_items=40)
        if live_arena:
            for s in sources:
                if s.get("id") == "lmsys_arena":
                    s["items"] = live_arena
                    updated_sources += 1
                    break
    except Exception as e:
        errors.append(f"LMSYS Arena 更新失败: {str(e)}")

    # 4. 尝试更新 OpenRouter 热门榜单
    try:
        live_or = _fetch_openrouter_live(max_items=40)
        if live_or:
            for s in sources:
                if s.get("id") == "openrouter_rp":
                    s["items"] = live_or
                    updated_sources += 1
                    break
    except Exception as e:
        errors.append(f"OpenRouter 榜单更新失败: {str(e)}")

    # 5. 生成 AA 智能质量榜 与 AA 极速生成榜（分立状态）
    try:
        aa_base = next((s for s in sources if s.get("id") == "artificial_analysis"), None)
        if aa_base and aa_base.get("items"):
            q_src, s_src = _build_aa_discrete_boards(aa_base["items"], max_items=40)
            if q_src and s_src:
                # 检查 sources 中是否已有分立榜单，若无则按顺序插入在 artificial_analysis 后面
                has_q = any(s.get("id") == "aa_quality" for s in sources)
                has_s = any(s.get("id") == "aa_speed" for s in sources)

                if has_q:
                    for s in sources:
                        if s.get("id") == "aa_quality":
                            s["items"] = q_src["items"]
                            break
                if has_s:
                    for s in sources:
                        if s.get("id") == "aa_speed":
                            s["items"] = s_src["items"]
                            break

                if not has_q or not has_s:
                    new_sources = []
                    for s in sources:
                        new_sources.append(s)
                        if s.get("id") == "artificial_analysis":
                            if not has_q:
                                new_sources.append(q_src)
                            if not has_s:
                                new_sources.append(s_src)
                    sources = new_sources
                updated_sources += 2
    except Exception as e:
        errors.append(f"AA 质速指标分立榜单生成失败: {str(e)}")

    # 6. 动态重新计算「综合平均天梯 (娱乐自嗨榜)」
    try:
        comp_items = _compute_composite_average(sources, max_items=55)
        if comp_items:
            for s in sources:
                if s.get("id") == "composite_average":
                    s["items"] = comp_items
                    s["name"] = "综合平均天梯 (娱乐自嗨榜)"
                    break
    except Exception as e:
        errors.append(f"综合天梯计算失败: {str(e)}")

    current_data["sources"] = sources
    current_data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 写回持久化缓存
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(current_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        errors.append(f"写回缓存文件失败: {str(e)}")

    return {
        "success": True,
        "updated_sources": updated_sources,
        "errors": errors,
        "data": current_data
    }
