"""Tests for data.py pure logic — no DB or network required."""

import pytest
from data import (
    fold_actions, Action, ActionKind,
    Lazy, dictToCommandData, EventType, CommandData, Message, InvocationLog
)


# ---------------------------------------------------------------------------
# fold_actions
# ---------------------------------------------------------------------------

class TestFoldActions:
    def test_empty(self):
        assert fold_actions([]) == []

    def test_single(self):
        a = Action(kind=ActionKind.NEW_MESSAGE, text='hello')
        assert fold_actions([a]) == [a]

    def test_merges_same_kind(self):
        a1 = Action(kind=ActionKind.NEW_MESSAGE, text='hello')
        a2 = Action(kind=ActionKind.NEW_MESSAGE, text='world')
        result = fold_actions([a1, a2])
        assert len(result) == 1
        assert result[0].text == 'hello\nworld'

    def test_does_not_merge_different_kinds(self):
        a1 = Action(kind=ActionKind.NEW_MESSAGE, text='hello')
        a2 = Action(kind=ActionKind.REPLY, text='world')
        result = fold_actions([a1, a2])
        assert len(result) == 2

    def test_does_not_merge_react_emoji(self):
        a1 = Action(kind=ActionKind.REACT_EMOJI, text='👍')
        a2 = Action(kind=ActionKind.REACT_EMOJI, text='❤️')
        result = fold_actions([a1, a2])
        assert len(result) == 2

    def test_merges_multiple_then_different(self):
        actions = [
            Action(kind=ActionKind.NEW_MESSAGE, text='a'),
            Action(kind=ActionKind.NEW_MESSAGE, text='b'),
            Action(kind=ActionKind.REPLY, text='c'),
        ]
        result = fold_actions(actions)
        assert len(result) == 2
        assert result[0].text == 'a\nb'
        assert result[1].text == 'c'


# ---------------------------------------------------------------------------
# Lazy
# ---------------------------------------------------------------------------

class TestLazy:
    def test_evaluates_on_repr(self):
        called = []
        def fn():
            called.append(1)
            return 'result'
        lz = Lazy(fn)
        assert repr(lz) == 'result'
        assert len(called) == 1

    def test_sticky_caches(self):
        count = [0]
        def fn():
            count[0] += 1
            return 'val'
        lz = Lazy(fn, stick=True)
        repr(lz)
        repr(lz)
        assert count[0] == 1

    def test_non_sticky_re_evaluates(self):
        count = [0]
        def fn():
            count[0] += 1
            return f'val{count[0]}'
        lz = Lazy(fn, stick=False)
        repr(lz)
        repr(lz)
        assert count[0] == 2


# ---------------------------------------------------------------------------
# dictToCommandData
# ---------------------------------------------------------------------------

class TestDictToCommandData:
    def test_minimal(self):
        data = {'pattern': '!hello', 'actions': []}
        cmd = dictToCommandData(data)
        assert cmd.pattern == '!hello'
        assert cmd.event_type == EventType.message
        assert cmd.discord is True
        assert cmd.twitch is True

    def test_event_type_enum_cast(self):
        data = {
            'pattern': 'redeem',
            'event_type': 'twitch_reward_redemption',
            'actions': []
        }
        cmd = dictToCommandData(data)
        assert cmd.event_type == EventType.twitch_reward_redemption

    def test_full(self):
        data = {
            'pattern': '!test',
            'event_type': 'message',
            'discord': False,
            'twitch': True,
            'name': 'test_cmd',
            'mod': True,
            'hidden': True,
            'actions': [{'kind': 'message', 'text': 'hi'}],
            'version': 2,
        }
        cmd = dictToCommandData(data)
        assert cmd.discord is False
        assert cmd.mod is True
        assert cmd.hidden is True
        assert cmd.version == 2
        assert len(cmd.actions) == 1
        assert cmd.actions[0].kind == ActionKind.NEW_MESSAGE
