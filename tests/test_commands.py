import pytest
import json
import re
from unittest.mock import MagicMock

from commands import (
    PersistentCommand,
    HelpCommand,
    SetCommand,
    Eval,
    morph_text
)
from data import dictToCommandData, ActionKind, EventType

def test_morph_text_inflection():
    # Test pymorph via txt() inflection-related `morph_text` util.
    # When text is processed for adding to DB, it gets inflected into cases.
    res = morph_text("синий кот")
    assert res is not {}
    assert 'рд' in res  # Genitive
    assert 'дт' in res  # Dative
    assert 'тв' in res  # Instrumental
    # Checking specific inflections:
    # "синий кот" -> "синего кота" (Gen/Acc animate)
    assert res['рд'] == "синего кота"
    assert res['дт'] == "синему коту"
    assert res['тв'] == "синим котом"

    # Test handling words that cannot be morphed or shouldn't be
    res2 = morph_text("hello 123")
    assert res2 == {}

def test_persistent_command_formats():
    # Example commands from /tmp/commands.txt
    cmd_json = {
        "mod": False,
        "help": "",
        "name": "laud",
        "hidden": False,
        "twitch": True,
        "actions": [
            {
                "kind": "message",
                "text": "{{ mention }} you are {{ txt('adj') }} {{ txt('adj') }} {{ txt('noun') }}",
                "attachment": "",
                "attachment_name": ""
            }
        ],
        "discord": True,
        "pattern": "!prefixlaud\\b",
        "version": 1,
        "help_full": "",
        "event_type": "message"
    }

    cmd_data = dictToCommandData(cmd_json)
    cmd = PersistentCommand(cmd_data, "!")

    assert cmd.data.name == "laud"
    assert cmd.for_discord() is True
    assert cmd.for_twitch() is True
    assert not cmd.mod_only()
    assert not cmd.hidden_help()
    
    # Assert regex compiles correctly with prefix replacement
    # "!prefixlaud\b" -> "! ?laud\b" (with re.IGNORECASE)
    assert cmd.regex.pattern == "! ?laud\\b"
    assert cmd.regex.flags & re.IGNORECASE

    # "help" defaults to prefix + name if empty in config
    assert cmd.help("!") == "!laud"
    assert cmd.help_full("!") == "!laud"

def test_persistent_command_custom_help():
    cmd_json = {
        "mod": False,
        "help": "!prefixhug",
        "help_full": "!prefixhug <имя человека>",
        "name": "hug",
        "hidden": False,
        "twitch": True,
        "actions": [{"kind": "message", "text": "test"}],
        "discord": True,
        "pattern": "!prefix(hug|обнять)\\b",
        "version": 1
    }
    cmd_data = dictToCommandData(cmd_json)
    cmd = PersistentCommand(cmd_data, "!")
    
    assert cmd.help("!") == "!hug"
    assert cmd.help_full("!") == "!hug <имя человека>"

    # Help substitution
    cmd = PersistentCommand(cmd_data, "?")
    assert cmd.help("?") == "?hug"
    assert cmd.help_full("?") == "?hug <имя человека>"

def test_builtin_commands_help_full():
    help_cmd = HelpCommand()
    assert help_cmd.help("!") == "!help [<command name>]"
    assert help_cmd.help_full("!") == "!help [<command name>]"

    eval_cmd = Eval()
    assert eval_cmd.help("-") == "-eval"
    assert eval_cmd.help_full("-").startswith("-eval <expression>")

    set_cmd = SetCommand()
    hf = set_cmd.help_full("!")
    assert "!command [<name> <template>|<JSON>]" in hf
    assert "Variables available:" in hf
    assert "- get(<name>" in hf
    assert "- set(<name>" in hf
