import unicodedata
from dataclasses import dataclass


def normalize_keyword(text: str) -> str:
    keyword = text.strip()
    if (
        not keyword or len(keyword) > 80 or keyword in {".", ".."}
        or any(c in keyword for c in '/\\:*?"<>|')
        or any(unicodedata.category(c).startswith("C") for c in keyword)
    ):
        raise ValueError("关键词需为1～80个字符，不能包含路径符号或控制字符。")
    return keyword


@dataclass(frozen=True)
class Command:
    kind: str
    keyword: str = ""


def parse_command(text: str) -> Command:
    text = text.strip()
    if text == "/帮助":
        return Command("help")
    if text == "/取消":
        return Command("cancel")
    parts = text.split(maxsplit=1)
    if parts and parts[0] == "/加图":
        if len(parts) != 2:
            raise ValueError("用法：/加图 关键词，然后在提示的有效期内发送图片。")
        return Command("add", normalize_keyword(parts[1]))
    if text.startswith("/"):
        return Command("unknown")
    try:
        return Command("lookup", normalize_keyword(text))
    except ValueError:
        return Command("ignore")
