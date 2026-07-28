"""Conservative, deterministic smart categories for clipboard records."""
import re


CATEGORY_LABELS = {
    "code": "代码",
    "todo": "待办",
    "prompt": "提示词",
    "contact": "联系方式",
    "path": "文件路径",
    "sensitive": "敏感内容",
}

_SENSITIVE = re.compile(
    r"(?i)(?:\b(?:password|passwd|pwd|api[ _-]?key|access[ _-]?token|secret)\b|"
    r"密码|验证码|校验码)\s*[:=：]\s*\S{4,}|"
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,})\b"
)
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_CONTACT_WORD = re.compile(r"(?:微信号|WeChat|QQ)\s*[:：]?\s*[A-Za-z0-9_-]{5,}", re.I)
_WINDOWS_PATH = re.compile(r"(?i)(?:\b[A-Z]:\\|\\\\[^\\\s]+\\)[^\r\n<>|]*")
_UNIX_PATH = re.compile(r"(?<!\w)/(?:Users|home|var|etc|tmp|opt|mnt)/[^\s\r\n]+")
_CODE = re.compile(
    r"(?im)(?:^```|^\s*(?:def|class|function|const|let|var|import|from|return|if|for|while)\b|"
    r"^\s*(?:SELECT|INSERT|UPDATE|DELETE|CREATE)\s+.+\b(?:FROM|INTO|TABLE|SET)\b|"
    r"^\s*(?:git|npm|pnpm|yarn|pip|python|node|cargo|curl)\s+[-\w])"
)
_TODO = re.compile(r"(?i)(?:^|\s)(?:TODO|FIXME|待办|记得|别忘了|需要完成|稍后处理)\s*[:：-]?")
_PROMPT = re.compile(
    r"^(?:请你?|帮我|替我|给我|分析一下|总结一下|生成|写一(?:个|份|段)|改写|翻译|"
    r"根据.+(?:生成|写|分析|总结))",
    re.S,
)


def classify(kind, content):
    """Return one smart-category key, or an empty string when confidence is low."""
    if kind != "text":
        return ""
    value = str(content or "").strip()
    if not value:
        return ""
    if _SENSITIVE.search(value):
        return "sensitive"
    if _EMAIL.search(value) or _PHONE.search(value) or _CONTACT_WORD.search(value):
        return "contact"
    if _WINDOWS_PATH.search(value) or _UNIX_PATH.search(value):
        return "path"
    if _CODE.search(value) or (
        value.startswith(("{", "["))
        and value.endswith(("}", "]"))
        and (":" in value or '"' in value)
    ):
        return "code"
    if _TODO.search(value):
        return "todo"
    if _PROMPT.search(value):
        return "prompt"
    return ""
