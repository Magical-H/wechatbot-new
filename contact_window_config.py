"""联系人独立窗口配置：文件只保存规范 username，不保存运行时 HWND。"""
import json
import os
import re
import sys


def contact_windows_path():
    base_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "contact_windows.json")


def load_contact_window_usernames(path=None):
    try:
        with open(path or contact_windows_path(), "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return []
    usernames = data.get("usernames", []) if isinstance(data, dict) else data
    if not isinstance(usernames, list):
        return []
    return list(dict.fromkeys(value.strip() for value in usernames if isinstance(value, str) and value.strip()))


def save_contact_window_usernames(usernames, path=None):
    path = path or contact_windows_path()
    temporary_path = path + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as file:
        json.dump({"usernames": usernames}, file, ensure_ascii=False, indent=2)
    os.replace(temporary_path, path)


def classify_contact_username(username):
    value = (username or "").strip()
    lowered = value.casefold()
    if lowered.endswith("@chatroom"):
        return "groups"
    if lowered.endswith("@openim"):
        return "enterprise"
    if lowered.startswith("gh_"):
        return "official"
    if lowered in {
        "filehelper", "fmessage", "weixin", "weixinreminder", "tmessage",
        "qmessage", "medianote", "newsapp", "qqmail", "floatbottle",
        "brandsessionholder", "notification_messages", "linkedinplugin",
        "masssendapp", "feedsapp", "shakeapp", "lbsapp", "voipapp", "weibo",
    }:
        return "special"
    if lowered.startswith("wxid_") or re.fullmatch(r"[a-z0-9_-]{6,64}", value, re.IGNORECASE):
        return "personal"
    return "other"
