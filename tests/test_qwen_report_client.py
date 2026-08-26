import unittest
from unittest.mock import Mock, patch

import requests

from backend.reporting.contracts import OutlinePlan
from backend.reporting.errors import ReportModelError
from backend.reporting.qwen_report_client import QwenReportClient


class QwenReportClientTest(unittest.TestCase):
    def test_missing_api_key_never_falls_back_to_fake_output(self):
        client = QwenReportClient(api_key="")
        with self.assertRaises(ReportModelError) as raised:
            client.generate_structured(messages=[], response_model=OutlinePlan)
        self.assertEqual(raised.exception.kind, "configuration")
        self.assertFalse(raised.exception.retryable)

    @patch("backend.reporting.qwen_report_client.requests.post")
    def test_timeout_is_classified_as_retryable(self, post):
        post.side_effect = requests.Timeout("slow")
        client = QwenReportClient(api_key="test-key", timeout=1)
        with self.assertRaises(ReportModelError) as raised:
            client.generate_structured(messages=[], response_model=OutlinePlan)
        self.assertEqual(raised.exception.kind, "timeout")
        self.assertTrue(raised.exception.retryable)

    @patch("backend.reporting.qwen_report_client.requests.post")
    def test_structured_output_is_validated(self, post):
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "model": "qwen-test",
            "choices": [
                {
                    "message": {
                        "content": """
                        {"report_title":"测试报告","executive_summary_focus":"重点", "sections":[
                          {"section_id":"s1","section_kind":"overview","title":"一","purpose":"一"},
                          {"section_id":"s2","section_kind":"risk_analysis","title":"二","purpose":"二"},
                          {"section_id":"s3","section_kind":"risk_analysis","title":"三","purpose":"三"},
                          {"section_id":"s4","section_kind":"case_analysis","title":"四","purpose":"四"},
                          {"section_id":"s5","section_kind":"synthesis","title":"五","purpose":"五"},
                          {"section_id":"s6","section_kind":"conclusion","title":"六","purpose":"六"}
                        ]}
                        """
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"total_tokens": 12},
        }
        post.return_value = response
        client = QwenReportClient(api_key="test-key")
        result = client.generate_structured(messages=[], response_model=OutlinePlan)
        self.assertEqual(result.model, "qwen-test")
        self.assertEqual(result.usage["total_tokens"], 12)
        self.assertEqual(len(result.output["sections"]), 6)


if __name__ == "__main__":
    unittest.main()
