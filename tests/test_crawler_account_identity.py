import unittest

from backend.audit_agent.crawler_account_identity import (
    candidate_is_personal_link,
    extract_platform_account_id,
)


class CrawlerAccountIdentityTest(unittest.TestCase):
    def test_extracts_supported_profile_ids(self):
        self.assertEqual(
            extract_platform_account_id(
                "xhs",
                "https://www.xiaohongshu.com/user/profile/5f58bd990000000001003753?xsec_source=pc_feed",
            ),
            "5f58bd990000000001003753",
        )
        self.assertEqual(
            extract_platform_account_id(
                "dy",
                "https://www.douyin.com/user/MS4wLjABAAAA_test-user?from_tab_name=main",
            ),
            "MS4wLjABAAAA_test-user",
        )
        self.assertEqual(
            extract_platform_account_id(
                "ks",
                "https://www.kuaishou.com/profile/3x84qugg4ch9zhs",
            ),
            "3x84qugg4ch9zhs",
        )

    def test_rejects_non_profile_urls(self):
        self.assertEqual(
            extract_platform_account_id(
                "xhs",
                "https://www.xiaohongshu.com/explore/5f58bd990000000001003753",
            ),
            "",
        )
        self.assertEqual(
            extract_platform_account_id("dy", "https://www.douyin.com/video/123456789"),
            "",
        )

    def test_douyin_candidate_must_be_top_level_personal_link(self):
        header_avatar = {
            "visible": True,
            "top": 48,
            "hasImage": True,
            "inContent": False,
            "text": "",
            "label": "",
        }
        feed_author = {**header_avatar, "top": 420}
        content_header = {**header_avatar, "inContent": True}
        unlabeled_fallback = {**header_avatar, "hasImage": False}
        labeled_fallback = {**unlabeled_fallback, "label": "个人主页"}

        self.assertTrue(candidate_is_personal_link("dy", header_avatar, False))
        self.assertFalse(candidate_is_personal_link("dy", feed_author, False))
        self.assertFalse(candidate_is_personal_link("dy", content_header, False))
        self.assertFalse(candidate_is_personal_link("dy", unlabeled_fallback, True))
        self.assertTrue(candidate_is_personal_link("dy", labeled_fallback, True))


if __name__ == "__main__":
    unittest.main()
