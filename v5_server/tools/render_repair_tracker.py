"""Render the project repair ledger; never silently overwrite an existing file."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "v5_server/docs/repair_tracker.json"


def render(data):
    findings = data["findings"]
    count = len(findings)
    assert count >= 381
    assert {v["id"] for v in findings} == {f"A{i:02d}" for i in range(1, count + 1)}
    statuses = data["status_labels"]
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    def link(path, title="记录"):
        return f"[{title}]({path})"
    lines = ["# 问题与修复总清单", "", f"更新：{data['updated_at']}。维护数据源："
             + link("v5_server/docs/repair_tracker.json", "结构化台账") + "。", "",
             f"这里串联公开 Issue、A01—A{count} 历史发现、13个修复分组与每张实际修复票。{count}是历史编号覆盖数，"
             f"不是{count}个当前确认故障；待复核、范围排除、政策待定、源码通过与实机通过分开记录。", "",
             "原报告限制完整保留在台账的 original_evidence 字段；修复前仍须回到原报告核对。"
             "未进入13分组的领域问题仍在下方逐项表中，不会因此漏出清单。", "",
             "## 当前优先顺序", "", "| 顺序 | 工作 | 依赖与验收 |", "| --- | --- | --- |"]
    lines += [f"| {i} | {cell(v['work'])} | {cell(v['acceptance'])} |" for i, v in enumerate(data["next_steps"], 1)]
    lines += ["", "每票流程：核原报告和当前源码 → 桌面原件备份 → 修前复现/正常对照 → 限定修复 → "
              "内存或测试库验证SQL与出站帧 → 本地提交 → 测试服加载 → 用户实机验收。"
              "步骤证据不齐时保留未完成状态，禁止用历史探针通过代替修后通过。", "",
              "备份统一位于桌面“服务端修复备份”，每票子目录保存相对路径、原件和SHA256。"
              "数据库文件写入前仍在项目根db_backup另备份。更新台账和本清单前也应保存原件。", "",
              "维护：修改 repair_tracker.json 的状态/步骤/提交，再运行 "
              "`python -B -X utf8 v5_server/tools/render_repair_tracker.py --output 修复总清单.md "
              "--backup-dir <本票桌面备份目录>`。渲染器不会无备份覆盖现有清单。", "",
              "## 已处理票与分步记录", ""]
    for ticket in data["tickets"]:
        lines += [f"### {ticket['id']}：{ticket['title']}", "", cell(ticket["summary"]), "",
                  f"状态：{statuses[ticket['status']]}；源码提交：{'、'.join(ticket['commits']) or '待提交'}。", "",
                  "| 日期 | 步骤 | 结果/证据 |", "| --- | --- | --- |"]
        lines += [f"| {step['date']} | {cell(step['step'])} | {cell(step['result'])} |" for step in ticket["steps"]]
        lines += ["", link(ticket["report"], "完整说明"), ""]
    lines += ["## 13个公共/尾链修复分组", "", "分组没有覆盖全部领域历史发现；逐项清单为完整编号入口。", "",
              "| 分组 | 范围 | 前置 | 当前状态 |", "| --- | --- | --- | --- |"]
    for group in data["groups"]:
        lines.append(f"| {group['id']} {cell(group['name'])} | {'、'.join(group['findings'])} | "
                     f"{'、'.join(group['needs']) or '—'} | {cell(group['repair_status'])} |")
    lines += ["", "## 公开 Issue 与子项", "", "状态来自已保存的首轮修复报告，未重新联网更新Issue状态、推送或关闭Issue。", "",
              "| 编号 | 事项 | 源码状态 | 保留事项 |", "| --- | --- | --- | --- |"]
    for issue in data["github"]:
        lines.append(f"| [{issue['id']}]({issue['url']}) | {cell(issue['summary'])} | "
                     f"{statuses[issue['status']]} | {cell(issue['remaining'])} |")
    lines += ["", "来源：" + link("v5_server/docs/repair_20261018_round1_github_issues.md", "首轮公开Issue修复报告"), "",
              "## 其他已处理事项", "", "| 事项 | 记录 | 状态 |", "| --- | --- | --- |"]
    for item in data["additional"]:
        lines.append(f"| {cell(item['title'])} | {link(item['report'])} | {cell(item['status'])} |")
    lines += ["", f"## A01—A{count}逐项入口", "", "默认“历史待复核”仅表示尚未建立当前修复票，不推翻原报告中的排除/条件/政策边界。"
              "部分修复不标记整条完成；原生客户端未验收时单独保留。", "",
              "| 编号 | 问题/领域 | 状态 | 分组 | 修复票/提交 | 原证据 |", "| --- | --- | --- | --- | --- | --- |"]
    for finding in findings:
        reports = finding["reports"]
        source = "v5_server/docs/" + (reports[0] if reports else "health_audit_index.md")
        title = finding["summary"]
        if len(title) > 100:
            title = title[:100] + "…"
        lines.append(f"| {finding['id']} | {cell(title)}（{cell(finding['domain'])}） | "
                     f"{statuses[finding['status']]} | {'、'.join(finding['groups']) or '未归入R组'} | "
                     f"{'、'.join(finding['tickets']) or '—'} | {link(source, '报告')} |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    text = render(json.loads(LEDGER.read_text("utf-8")))
    if args.output is None:
        print(text, end="")
        return
    target = args.output.resolve()
    target.relative_to(ROOT)  # Output belongs to the project.
    if target.exists():
        if args.backup_dir is None:
            parser.error("Existing output requires --backup-dir before overwriting")
        args.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup = args.backup_dir / (stamp + "_" + target.name)
        shutil.copy2(target, backup)
        if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(backup.read_bytes()).digest():
            raise RuntimeError("Backup verification failed")
    target.write_text(text, encoding="utf-8")
    print(f"Rendered {len(text)} characters to {target.name}")


if __name__ == "__main__":
    main()
