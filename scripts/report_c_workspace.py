"""Create the historical workspace manifest only from a verified published C."""
import argparse
import json
import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--handoff", required=True, type=Path)
    args = parser.parse_args()
    from backend.reporting.store import ReportStore
    from backend.reporting.integration_source import source_sha256
    runtime = args.runtime.resolve()
    receipt = json.loads((runtime / "published.json").read_text())
    source = runtime / "report-generation.sqlite3"
    store = ReportStore(source)
    full = store.get_full_version(receipt["report_version_id"])
    assert full["status"] == "published"
    snapshot = store.load_immutable_snapshot(receipt["report_version_id"])
    assert len(snapshot.posts) == 219 and snapshot.statistics["decision"] == {"pass": 181, "review": 38}
    archive = runtime / "report-c-published.sqlite3"
    if not archive.exists():
        with sqlite3.connect(source) as src, sqlite3.connect(archive) as dest:
            src.backup(dest)
    summary = json.loads((args.handoff / "source-summary.json").read_text())
    jobs = summary["configured_jobs"]
    rules = jobs[0]["rule_snapshot"]
    prompt_sources = []
    for job in jobs:
        prompt_sources.append(f"来源任务：{job['id']}\n配置修订：{job['current_audit_config_revision_id']}\n" + json.dumps(job["prompt_profile_snapshot"], ensure_ascii=False, indent=2))
    details = [
        {"title": "实际来源词与覆盖范围", "text": "维汉通婚18帖；维汉婚姻83帖；维族家里不同意39帖；维汉夫妻79帖。23+196=219个唯一已完成帖，另5帖未完成。存储评论47044条、完成47043条、失败1条；需翻译689条、完成688条、失败1条。"},
        {"title": "旧资料配置：17个召回词", "text": "正式召回库引用：ethnic_discussion_recall_v1\n配置词：" + "、".join(jobs[0]["lexicon_keywords"]) + "\n实际返回：维汉通婚18帖、维汉婚姻5帖。"},
        {"title": "新增资料配置：3个来源词", "text": "配置词：" + jobs[1]["keyword"] + "\n直接审核已保存资料；library_ids与lexicon_keywords均为空，未引用正式召回词库。完成结果来自维族家里不同意39帖、维汉婚姻78帖、维汉夫妻79帖。"},
        {"title": "业务规则：13条与豁免边界", "text": "民族内容审核 K2：自豪表达与对象范围边界（试用）\n" + json.dumps(rules, ensure_ascii=False, indent=2)},
        {"title": "两组审核 Prompt 来源与配置修订", "text": "两轮规则相同，翻译实现存在差异；未使用新Prompt重审全部帖子。\n\n" + "\n\n".join(prompt_sources)},
        {"title": "原始会话与执行记录", "text": "旧资料会话：http://127.0.0.1:3148/investigation/investigation-session%3Af8bc8ee7bec3438a8716a329e13f49e5\n新增资料会话：http://127.0.0.1:3148/investigation/investigation-session%3A3919bd36fe4a4101a714ad5b86e25569\n原始对话、审核执行与失败记录均保留在来源环境；本窗口前置对话为历史材料整理展示，后续提问为真实持久化对话。"},
    ]
    timeline = (
        ("request", "user_request", "帮我看看抖音上关于维汉婚姻和家庭相处的讨论，重点看看有没有针对民族身份的辱骂、歧视或煽动对立，整理一份有具体依据的报告。"),
        ("plan", "plan_recommendation", "我会从维汉通婚、维汉婚姻、家人反对和夫妻相处等讨论入手，结合帖子、视频和评论核对。普通的婚姻选择、家庭经历和文化表达会与明确的攻击区分开。你可以在配置里查看搜索词和判断标准。"),
        ("confirm", "user_confirmation", "可以，帖子和评论都看一下，把有代表性的内容和依据整理出来。"),
        ("process", "processing_update", "已核对已有帖子和评论资料，并完成代表内容与证据的整理。报告保留了未完成内容和缺失译文的说明。"),
        ("ready", "report_ready", "报告已生成，覆盖219个已完成帖。可以打开报告查看主要发现、代表内容和原文依据，也可以继续提问。"),
    )
    manifest = dict(workspace_id="historical-report-c", run_id="historical-report-run-c", title="维汉婚姻与家庭讨论 · Report C", task_id=snapshot.task_id, report_version_id=receipt["report_version_id"], source_database=str(archive), source_database_sha256=source_sha256(archive), report_content_hash=receipt["content_hash"], snapshot_hash=snapshot.snapshot_hash,
        draft={"task_name": "维汉婚姻与家庭讨论内容分析", "subject": "识别民族关系讨论中的身份攻击、歧视与对立表达，并保留正常表达边界", "platform": "dy", "search_terms": ["维汉通婚", "维汉婚姻", "维族家里不同意", "维汉夫妻"], "analysis_plan": "民族关系讨论中的攻击与歧视识别", "analysis_description": "结合帖子、视频和评论既有审核依据，区分明确攻击与普通婚姻选择、民族自豪及文化差异。", "recall_lexicons": [], "history_notice": "前置对话为历史资料整理展示；原始会话和执行记录保留，后续问答实时生成并保存。", "scope_description": "219个唯一已完成帖；另5帖未完成，1条评论翻译失败。仅汇总既有资料，不含另行采集的维汉一家亲。", "configuration_details": details},
        display_timeline=[{"id": f"history-c-{key}", "kind": kind, "occurred_at": full["published_at"] if kind == "report_ready" else "", "content": content} for key, kind, content in timeline])
    target = runtime / "workspace-manifest.json"
    encoded = json.dumps(manifest, ensure_ascii=False, indent=2)
    if target.exists() and target.read_text() != encoded:
        raise ValueError("refusing to rebind historical Report C manifest")
    target.write_text(encoded)
    print(target)


if __name__ == "__main__":
    main()
