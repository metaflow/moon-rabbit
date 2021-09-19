"""
 Copyright 2021 Goncharov Mikhail

 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at

      https://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
 """

import dataclasses
from enum import Enum
from typing import Callable, Dict, List, Optional, Protocol, Tuple
import logging
import dacite
from dacite.config import Config
from jinja2.sandbox import SandboxedEnvironment

templates = SandboxedEnvironment()


def render(text: str, vars: Dict):
    return templates.from_string(text).render(vars).strip()


@dataclasses.dataclass
class TemplateVariables:
    mention: str


class ActionKind(str, Enum):
    NOOP = 'noop'
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
    help_full: str = ''
    mod: bool = False
    hidden: bool = False # don't show in !help
    actions: List[Action] = dataclasses.field(default_factory=list)
    version: int = 1


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


def fold_actions(actions: List[Action]) -> List[Action]:
    last: Optional[Action] = None
    z: List[Action] = []
    for a in actions:
        if not last:
            last = a
            continue
        if a.kind != last.kind or last.kind == ActionKind.REACT_EMOJI:
            z.append(last)
            last = a
            continue
        last.text += '\n' + a.text
    if last:
        z.append(last)
    return z
