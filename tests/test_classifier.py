import unittest

from app.classifier import classify


class ClassifierTests(unittest.TestCase):
    def test_classifies_supported_text_categories(self):
        cases = {
            "sensitive": "api_key = sk-1234567890abcdef1234567890",
            "contact": "联系邮箱 hello@example.com",
            "path": r"文件在 C:\Users\Example\Documents\report.docx",
            "code": "def render_item(value):\n    return value.strip()",
            "todo": "TODO: 明天下午提交项目报告",
            "prompt": "帮我分析一下这段文章并给出三个标题",
        }
        for expected, content in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(classify("text", content), expected)

    def test_sensitive_category_has_priority_over_code(self):
        self.assertEqual(
            classify("text", "const token = 'ghp_1234567890abcdefghijklmnopqrst';"),
            "sensitive",
        )

    def test_does_not_force_categories_for_ordinary_or_non_text_items(self):
        self.assertEqual(classify("text", "今天天气不错"), "")
        self.assertEqual(
            classify("text", "这段说明提到了密码管理器、验证码过滤和隐私策略，但没有具体密钥。"),
            "",
        )
        self.assertEqual(classify("url", "https://example.com"), "")
        self.assertEqual(classify("image", ""), "")


if __name__ == "__main__":
    unittest.main()
