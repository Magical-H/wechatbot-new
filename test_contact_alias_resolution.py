# -*- coding: utf-8 -*-
"""共享联系人搜索的微信号身份确认回归测试，不启动微信或发送消息。"""
import unittest
from unittest.mock import patch

import wxbot
from wechatauto.uia_driver import RESULT_AID_PREFIX
from wechatauto.wx import WeChat as BaseWeChat


ALIAS = "magical_hz"
USERNAME = "wind_hz"
NICKNAME = "　　　　"
CONTACT = {
    "username": USERNAME,
    "nick_name": NICKNAME,
    "remark": "",
    "alias": ALIAS,
}


class _FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.result_rows = rows

    def execute(self, sql, params):
        if "WHERE alias = ?" in sql:
            self.result_rows = [row for row in self.rows if row.get("alias") == params[0]][:2]
        elif "WHERE nick_name = ? OR remark = ?" in sql:
            self.result_rows = [
                row for row in self.rows
                if params[0] in (row.get("nick_name"), row.get("remark"))
            ][:2]
        return self

    def fetchall(self):
        return self.result_rows

    def close(self):
        pass


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows

    def _contact_conn(self):
        return _FakeConnection(self.rows)

    def search_contact(self, query):
        hits = [
            row for row in self.rows
            if query in (row.get("nick_name"), row.get("remark"), row.get("username"), row.get("alias"))
        ]
        # Mirror wechatauto: search_contact results omit the alias column.
        return [{key: value for key, value in row.items() if key != "alias"} for row in hits]

    def get_nickname(self, username):
        row = next((row for row in self.rows if row.get("username") == username), None)
        return row.get("nick_name") if row else None


class _Rect:
    def __init__(self, left=110, top=120, right=300, bottom=160):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom


class _FakeControl:
    def __init__(self, name="", aid="", children=None, rect=None):
        self.Name = name
        self.AutomationId = aid
        self._children = children or []
        self.BoundingRectangle = rect or _Rect()

    def GetChildren(self):
        return self._children


class _FakeUIA:
    def __init__(self, results_by_keyword, display_name=NICKNAME, keywords=None):
        self.results_by_keyword = results_by_keyword
        self.display_name = display_name
        self.keywords = keywords or {}
        self.clicked = None
        self.chat_name = None
        self.search_visible = False
        self.open_calls = []
        self.back_calls = 0
        self.collect_calls = []

    def _collect_results(self, keyword, settle=1.5):
        self.collect_calls.append(keyword)
        return list(self.results_by_keyword.get(keyword, []))

    def back_to_chat_tab(self):
        self.back_calls += 1
        self.clicked = None
        self.chat_name = None
        self.search_visible = True
        return True

    def open_chat(self, keyword, index=None, section=None, retries=2):
        self.open_calls.append(keyword)
        if not self.back_to_chat_tab():
            return False
        results = []
        used_keyword = keyword
        for candidate in self.keywords.get(keyword, [keyword]):
            for _ in range(max(1, retries)):
                results = self._collect_results(candidate)
                if results:
                    used_keyword = candidate
                    break
            if results:
                break
        if not results:
            return False
        if section:
            filtered = [row for row in results if row.get("section") == section]
            if filtered:
                results = filtered
        chosen = results[0]
        self.clicked = chosen
        self.search_visible = False
        self.chat_name = self.display_name
        current = self.current_chat()
        return bool(current and (current == chosen["name"] or current == used_keyword))

    def current_chat(self):
        value = (self.chat_name or "").strip()
        return value or None

    def _find_search_list(self, timeout=3.0):
        return object() if self.search_visible else None

    def _chat_input(self):
        return _FakeControl(name=self.chat_name) if self.clicked is not None else None

    @staticmethod
    def _window_kind(_uia):
        return "main"


class _FakeGUI:
    def __init__(self, uia, ocr=None):
        self.uia = uia
        self.ocr = ocr or []
        self.ocr_calls = []
        self.origin_x = 100
        self.origin_y = 100
        self.render_w = 800
        self.render_h = 600
        self._current_chat = None

    def _get_uia(self, refresh=False):
        return self.uia

    def open_chat(self, name, exact=False):
        if self._get_uia().open_chat(name):
            self._current_chat = name
            return True
        return False

    def get_input_box(self):
        return (1, 2, 3, 4) if self.uia.clicked is not None else None

    @staticmethod
    def _window_kind(_uia):
        return "main"

    @staticmethod
    def _update_render_rect():
        pass

    def ocr_zoomed(self, rect, scale=3):
        self.ocr_calls.append((rect, scale))
        return self.ocr


def _result(contact, control, section="联系人", name=None):
    return {
        "cell": control,
        "name": contact.get("nick_name", "").strip() if name is None else name,
        "section": section,
        "aid": RESULT_AID_PREFIX + contact.get("nick_name", ""),
        "index": 0,
    }


def _bot(rows=None, results_by_keyword=None, ocr=None, display_name=NICKNAME, keywords=None):
    rows = rows or [CONTACT]
    uia = _FakeUIA(results_by_keyword or {}, display_name=display_name, keywords=keywords)
    gui = _FakeGUI(uia, ocr=ocr)
    bot = wxbot.WeChat.__new__(wxbot.WeChat)
    bot._db = _FakeDB(rows)
    bot._gui = gui
    bot._main_wechat_hwnd = 1
    bot._current_chat = None
    bot.who = "self"
    bot._wxid = "self-wxid"
    bot._install_alias_search()
    return bot, uia, gui


class ContactAliasResolutionTests(unittest.TestCase):
    def test_resolves_only_exact_alias_to_internal_username(self):
        bot, _, _ = _bot()
        self.assertEqual(bot._resolve_uname(ALIAS), USERNAME)
        self.assertIsNone(bot._resolve_uname("magical"))

    def test_chatwith_uses_shared_uia_search_on_repeated_calls(self):
        local = _result(CONTACT, _FakeControl(name="微信号：magical_hz"))
        network = _result(CONTACT, _FakeControl(name="微信号：magical_hz"), section="搜索网络结果")
        bot, uia, _ = _bot(
            results_by_keyword={ALIAS: [local, network]},
        )

        class _Chat:
            def __init__(self, who, gui, db):
                self.who = who
                self._wxid = USERNAME

        with patch("wechatauto.wx.Chat", _Chat):
            results = [bot.ChatWith(ALIAS) for _ in range(3)]

        self.assertEqual(results, [ALIAS] * 3)
        self.assertEqual(uia.back_calls, 3)
        self.assertEqual(uia.open_calls, [ALIAS] * 3)
        self.assertIs(uia.clicked, local)
        self.assertFalse(bot._gui._wechatbot_alias_open_failed)

    def test_uia_reads_exact_labeled_id_before_ocr(self):
        local = _result(CONTACT, _FakeControl(name="微信号：magical_hz"))
        bot, uia, gui = _bot(results_by_keyword={ALIAS: [local]})

        self.assertTrue(gui.open_chat(ALIAS))
        self.assertIs(uia.clicked, local)
        self.assertEqual(gui.ocr_calls, [])

    def test_send_path_direct_uia_open_uses_the_same_alias_matcher(self):
        local = _result(CONTACT, _FakeControl(name="微信号：magical_hz"))
        bot, uia, gui = _bot(results_by_keyword={ALIAS: [local]})

        self.assertTrue(gui._get_uia().open_chat(ALIAS))
        self.assertIs(uia.clicked, local)
        self.assertEqual(uia.back_calls, 1)

    def test_ocr_is_used_for_local_row_when_uia_has_no_id(self):
        local = _result(CONTACT, _FakeControl(name=""))
        bot, uia, gui = _bot(
            results_by_keyword={ALIAS: [local]},
            ocr=[("微信号：magical_hz", (0, 0, 0, 0))],
        )

        self.assertTrue(gui.open_chat(ALIAS))
        self.assertIs(uia.clicked, local)
        self.assertEqual(gui.ocr_calls[0][1], 4)

    def test_network_result_cannot_confirm_local_contact(self):
        network = _result(CONTACT, _FakeControl(name="微信号：magical_hz"), section="搜索网络结果")
        bot, uia, gui = _bot(results_by_keyword={ALIAS: [network]})

        self.assertFalse(gui.open_chat(ALIAS))
        self.assertIsNone(uia.clicked)
        self.assertTrue(gui._wechatbot_alias_open_failed)

    def test_conflicting_uia_id_blocks_ocr_and_name_fallback(self):
        named_contact = dict(CONTACT, nick_name="Alice")
        local = _result(named_contact, _FakeControl(name="微信号：another_id"))
        bot, uia, gui = _bot(
            rows=[named_contact],
            results_by_keyword={ALIAS: [local], "Alice": [local]},
            ocr=[("微信号：magical_hz", (0, 0, 0, 0))],
            display_name="Alice",
            keywords={ALIAS: [ALIAS, "Alice"]},
        )

        self.assertFalse(gui.open_chat(ALIAS))
        self.assertIsNone(uia.clicked)
        self.assertEqual(gui.ocr_calls, [])

    def test_ocr_conflicting_id_blocks_name_fallback(self):
        named_contact = dict(CONTACT, nick_name="Alice")
        local = _result(named_contact, _FakeControl(name=""))
        bot, uia, gui = _bot(
            rows=[named_contact],
            results_by_keyword={ALIAS: [local], "Alice": [local]},
            ocr=[("微信号：magicalhz", (0, 0, 0, 0))],
            display_name="Alice",
            keywords={ALIAS: [ALIAS, "Alice"]},
        )

        self.assertFalse(gui.open_chat(ALIAS))
        self.assertIsNone(uia.clicked)

    def test_exact_id_can_fall_back_to_unique_display_name_if_unreadable(self):
        named_contact = dict(CONTACT, nick_name="Alice")
        local = _result(named_contact, _FakeControl(name=""))
        bot, uia, _ = _bot(
            rows=[named_contact],
            results_by_keyword={ALIAS: [local], "Alice": [local]},
            display_name="Alice",
            keywords={ALIAS: [ALIAS, "Alice"]},
        )

        self.assertTrue(bot._gui.open_chat(ALIAS))
        self.assertEqual(uia.collect_calls[-1], "Alice")
        self.assertIs(uia.clicked, local)

    def test_duplicate_display_name_cannot_confirm_alias_fallback(self):
        named_contact = dict(CONTACT, nick_name="Alice")
        duplicate = {
            "username": "other_user",
            "nick_name": "Alice",
            "remark": "",
            "alias": "other_alias",
        }
        local = _result(named_contact, _FakeControl(name=""))
        bot, uia, gui = _bot(
            rows=[named_contact, duplicate],
            results_by_keyword={ALIAS: [local], "Alice": [local]},
            display_name="Alice",
            keywords={ALIAS: [ALIAS, "Alice"]},
        )

        self.assertFalse(gui.open_chat(ALIAS))
        self.assertIs(uia.clicked, local)
        self.assertTrue(gui._wechatbot_alias_open_failed)

    def test_unknown_id_shaped_query_does_not_search_network(self):
        bot, uia, _ = _bot(rows=[], results_by_keyword={"unknown_id": []})
        engine = bot._gui._get_uia()

        self.assertFalse(engine.open_chat("unknown_id"))
        self.assertEqual(uia.open_calls, [])

    def test_internal_username_still_uses_original_search_fallback(self):
        contact = dict(CONTACT, nick_name="Alice")
        local = _result(contact, _FakeControl(name="Alice"))
        bot, uia, gui = _bot(
            rows=[contact],
            results_by_keyword={"Alice": [local]},
            display_name="Alice",
            keywords={USERNAME: [USERNAME, "Alice"]},
        )

        self.assertTrue(gui._get_uia().open_chat(USERNAME))
        self.assertEqual(uia.open_calls, [USERNAME])
        self.assertIs(uia.clicked, local)

    def test_ordinary_chat_and_send_interfaces_keep_their_existing_dispatch(self):
        bot, _, _ = _bot()
        with patch.object(BaseWeChat, "ChatWith", return_value="Alice") as chat:
            self.assertEqual(bot.ChatWith("Alice"), "Alice")
        chat.assert_called_once_with("Alice")

        with patch.object(BaseWeChat, "SendMsg", return_value="sent") as send:
            self.assertEqual(bot.SendMsg("hello", who="Alice", exact=True), "sent")
        send.assert_called_once_with("hello", who="Alice", exact=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
