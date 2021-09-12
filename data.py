import dataclasses
from enum import Enum
from typing import Dict, List, Optional
import logging
import re
import dacite
from dacite.config import Config

@dataclasses.dataclass
class TemplateVariables:
    mention: str


class ActionKind(str, Enum):
    REPLY = 'reply'
    NEW_MESSAGE = 'message'
    PRIVATE_MESSAGE = 'private_message'
    REACT_EMOJI = 'react_emoji'

@dataclasses.dataclass
class Action:
    kind: ActionKind
    text: str

@dataclasses.dataclass
class CommandData:
    pattern: str
    discord: bool = True
    twitch: bool = True
    name: str = ''
    help: str = ''
    actions: List[Action] = dataclasses.field(default_factory=list)
    version: int = 1

class Command:
    regex: Optional[re.Pattern]
    data: CommandData
    def __init__(self, data, prefix):
        self.data = data
        p =  data.pattern.replace('!prefix', prefix)
        self.regex = re.compile(p, re.IGNORECASE)

def toCommand(c: CommandData, prefix: str):
  p =  c.pattern.replace('!prefix', prefix)
  return Command(
    regex=re.compile(p, re.IGNORECASE),
    persistent=c)

def dictToCommandData(data: Dict) -> CommandData:
    return dacite.from_dict(CommandData, data, config=Config(cast=[Enum]))

class InvocationLog():
    def __init__(self, prefix):
        self.messages = []
        self.prefix = prefix + ' '

    def info(self, s):
        logging.info(self.prefix + s)
        self.messages.append((logging.INFO, s))

    def warning(self, s):
        logging.warning(self.prefix + s)
        self.messages.append((logging.WARNING, s))

    def debug(self, s):
        logging.debug(self.prefix + s)
        self.messages.append((logging.DEBUG, s))

    def error(self, s):
        logging.error(self.prefix + s)
        self.messages.append((logging.ERROR, s))
