import json
import unittest

from backend.reporting.prompts import (
    _metric_records_for_prompt,
    outline_messages,
    section_messages,
)


class ReportPromptTest(unittest.TestCase):
    def test_outline_keeps_dynamic_planning_in_investigation_perspective(self):
        messages = outline_messages(
            task={"task_id": "task-1", "task_name": "测试任务"},
            statistics={"metrics": []},
            finding_cards=[],
        )
        prompt = messages[-1]["content"]

        self.assertIn("调查对象、风险现象、行为模式、典型案例和综合研判", prompt)
        self.assertIn("不要规划“审核难点”", prompt)
        self.assertIn("调查对象的风险特征", prompt)

    def test_evidence_percentage_is_described_as_finding_coverage(self):
        metric = {
            "metric_key": "metric:comment-percentage",
            "metric_name": "percentage",
            "label": "evidence_type=comment 的 percentage",
            "value": 58.333333,
            "denominator": 24,
            "denominator_name": "current_filtered_finding_count",
            "group": {"evidence_type": "comment"},
            "percentage_basis": "finding_count",
        }
        record = _metric_records_for_prompt([metric])[0]

        self.assertEqual(record["label"], "包含评论证据的内容覆盖率")
        self.assertEqual(record["display_value"], "58.3%")
        self.assertEqual(record["denominator_display"], "24")
        self.assertEqual(record["denominator_label"], "当前过滤范围内的内容总数")
        self.assertIn("这是内容覆盖率", record["semantic_definition"])
        self.assertIn("判定依据来自该类证据的比例", record["forbidden_interpretations"])
        self.assertIn("该类证据的决策贡献度或权重", record["forbidden_interpretations"])

        messages = section_messages(
            task={"task_id": "task-1", "task_name": "测试任务"},
            section={"section_id": "risk", "section_kind": "risk_analysis"},
            metrics=[metric],
            findings=[],
        )
        payload = json.loads(messages[-1]["content"].split("输入：\n", 1)[1])
        self.assertEqual(
            payload["allowed_metrics"][0]["percentage_basis"], "finding_count"
        )
        self.assertIn("不得写成判定依据占比", messages[-1]["content"])

    def test_evidence_count_is_a_unit_count_not_a_coverage_rate(self):
        metric = {
            "metric_key": "metric:comment-count",
            "metric_name": "evidence_count",
            "label": "evidence_type=comment 的 evidence_count",
            "value": 14,
            "denominator": 56,
            "denominator_name": "current_filtered_evidence_count",
            "group": {"evidence_type": "comment"},
            "percentage_basis": "",
        }
        record = _metric_records_for_prompt([metric])[0]

        self.assertEqual(record["label"], "评论证据条数")
        self.assertIn("规范化证据单元数量", record["semantic_definition"])
        self.assertEqual(record["denominator_label"], "当前过滤范围内的规范化证据总数")


if __name__ == "__main__":
    unittest.main()
