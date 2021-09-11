import dataclasses
from enum import IntEnum
from typing import List, Optional
import logging
import re

@dataclasses.dataclass
class TemplateVariables:
    mention: str


class ActionKind(IntEnum):
    REPLY = 1
    NEW_MESSAGE = 2
    PRIVATE_MESSAGE = 3


@dataclasses.dataclass
class Action:
    kind: ActionKind
    text: str

@dataclasses.dataclass
class Effect:
    text: str
    kind: int

@dataclasses.dataclass
class Command:
    pattern: str
    regex: Optional[re.Pattern] = None
    effects: List[Effect] = dataclasses.field(default_factory=list)
    discord: bool = True
    twitch: bool = True
    name: str = ''


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
