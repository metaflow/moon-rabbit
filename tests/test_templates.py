import time
from unittest.mock import MagicMock, patch

import pytest

import commands
import templates
from data import render

templates.register_template_globals()


def test_template_randint():
    # Evaluate default randint
    res1 = render("{{ randint() }}", {})
    assert 0 <= int(res1) <= 100

    # Evaluate randint with bounds
    res2 = render("{{ randint(1, 5) }}", {})
    assert 1 <= int(res2) <= 5


def test_template_timestamp():
    # Evaluate timestamp function
    res = render("{{ timestamp() }}", {})
    now = int(time.time())
    assert abs(int(res) - now) <= 5


def test_template_dt():
    # dt returns first argument if media == 'discord' else the second argument
    res_d = render("{{ dt('D', 'T') }}", {"media": "discord"})
    assert res_d == "D"

    res_t = render("{{ dt('D', 'T') }}", {"media": "twitch"})
    assert res_t == "T"


def test_template_discord_name():
    # discord_name utilizes discord_literal to replace '<@!' with '<@'
    res = render("{{ discord_name('<@!123456>') }}", {})
    assert res == "<@123456>"


class MockDB:
    def __init__(self):
        self.variables = {}
        self.texts = {1: "apple", 2: "banana"}
        self.tags = {1: "рд", 2: "дт"}
        self.text_tags = {1: {1: "яблока", 2: "яблоку"}, 2: {1: "банана", 2: "банану"}}

    def get_variable(self, channel_id, name, category, default_value):
        key = (channel_id, name, category)
        return self.variables.get(key, default_value)

    def set_variable(self, channel_id, name, value, category, expires):
        key = (channel_id, name, category)
        if value == "":
            self.variables.pop(key, None)
        else:
            self.variables[key] = value

    def count_variables_in_category(self, channel_id, category):
        return sum(1 for k in self.variables if k[0] == channel_id and k[2] == category)

    def delete_category(self, channel_id, category):
        keys_to_delete = [k for k in self.variables if k[0] == channel_id and k[2] == category]
        for k in keys_to_delete:
            del self.variables[k]

    def list_variables(self, channel_id, category):
        return [
            (k[1], v) for k, v in self.variables.items() if k[0] == channel_id and k[2] == category
        ]

    def get_random_text_id(self, channel_id, tag_query):
        # simple mock
        if "apple" in tag_query:
            return 1
        if "banana" in tag_query:
            return 2
        return None

    def tag_by_value(self, channel_id):
        return {"рд": 1, "дт": 2}

    def get_text_tag_value(self, channel_id, text_id, tag_id):
        return self.text_tags.get(text_id, {}).get(tag_id, "")

    def get_text(self, channel_id, text_id):
        return self.texts.get(text_id, "")


@pytest.fixture
def mock_db():
    mdb = MockDB()
    with patch("templates.db", return_value=mdb), patch("commands.db", return_value=mdb):
        yield mdb


def test_template_get_set_variable(mock_db):
    ctx = {"channel_id": 42}

    # default value
    assert render("{{ get('foo', '', 'bar') }}", ctx) == "bar"

    # set value
    render("{{ set('foo', 'baz') }}", ctx)
    assert render("{{ get('foo') }}", ctx) == "baz"

    # delete value
    render("{{ set('foo') }}", ctx)
    assert render("{{ get('foo', '', 'fallback') }}", ctx) == "fallback"


def test_template_category_functions(mock_db):
    ctx = {"channel_id": 42}

    # set multiple in category
    render("{{ set('a', '1', 'mycat') }}", ctx)
    render("{{ set('b', '2', 'mycat') }}", ctx)
    render("{{ set('c', '3', 'othercat') }}", ctx)

    # size
    assert render("{{ category_size('mycat') }}", ctx) == "2"
    assert render("{{ category_size('othercat') }}", ctx) == "1"

    # list
    res = render("{{ list_category('mycat') | string }}", ctx)
    assert "('a', '1')" in res
    assert "('b', '2')" in res

    # delete
    render("{{ delete_category('mycat') }}", ctx)
    assert render("{{ category_size('mycat') }}", ctx) == "0"
    assert render("{{ category_size('othercat') }}", ctx) == "1"


def test_template_txt(mock_db):
    ctx = {"channel_id": 42, "_log": MagicMock(), "_render_depth": 0}

    # regular txt
    assert render("{{ txt('apple') }}", ctx) == "apple"

    # txt with inflection
    assert render("{{ txt('apple', 'рд') }}", ctx) == "яблока"

    # txt missing match
    assert render("{{ txt('missing') }}", ctx) == ""


def test_template_message_queue():
    class DummyMessage:
        def __init__(self):
            self.additionalActions = []

    dummy_msg = DummyMessage()
    msg_id = "test_msg_1"

    # temporarily patch commands.messages
    commands.messages.clear()
    commands.messages[msg_id] = dummy_msg

    try:
        ctx = {"_id": msg_id}
        # returns empty string but queues side effect
        res = render("{{ message('hello') }}{{ message('world') }}", ctx)
        assert res == ""
        assert len(dummy_msg.additionalActions) == 2
        assert dummy_msg.additionalActions[0].text == "hello"
        assert dummy_msg.additionalActions[1].text == "world"
    finally:
        commands.messages.clear()


def test_template_complex_interaction_state(mock_db):
    ctx = {"channel_id": 77}

    # Simulates a stateful loop counting clicks in a text template snippet
    template = """
    {% set current = get('clicks', '', '0') | int %}
    {% set next = current + 1 %}
    {{ set('clicks', next | string) }}
    You clicked {{ next }} times!
    """

    res1 = render(template, ctx)
    assert "You clicked 1 times!" in res1
    assert mock_db.variables[(77, "clicks", "")] == "1"

    res2 = render(template, ctx)
    assert "You clicked 2 times!" in res2
    assert mock_db.variables[(77, "clicks", "")] == "2"
