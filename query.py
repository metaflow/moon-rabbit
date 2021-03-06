"""
 Copyright 2021 Google LLC

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

from typing import Dict, List, Set
import lark
import re
import logging
from lark.lexer import TerminalDef
from lark.tree import Tree
from lark.visitors import Transformer

query_grammar = """
?start: and -> start
?and: or | and "&" or | and "and"i or
?or: atom | or "|" atom | or "," atom | or "or"i atom
?atom: NAME | "(" and ")" | "not" atom -> not
NAME: /(\w|[-.,])+/
%import common.WS
%ignore WS
"""

query_parser = lark.Lark(query_grammar)
# tag_re = re.compile('[a-z0-9-_а-я]+', re.IGNORECASE)
_tag = re.compile('^(\w|[-.,])+$', re.IGNORECASE)

def good_tag_name(s: str) -> bool:
    s = s.strip().lower()
    if s in ['', 'and', 'or', 'not', '&', '|', '(', ')'] or not _tag.match(s):
        return False
    return True

class Normalize(Transformer):
    def __init__(self, tags: Dict[str, int]) -> None:
        self.tags = tags
        super().__init__(visit_tokens=True)

    def NAME(self, tk: lark.Token):
        return lark.Token('TAG', self.tags[tk.value])

def parse_query(tags: Dict[str, int], txt: str) -> lark.Tree:
    t = query_parser.parse(txt)
    return Normalize(tags).transform(t)

class Matcher(Transformer):
    def __init__(self, tags: Set[int]) -> None:
        self.tags = tags
        super().__init__(visit_tokens=True)

    def __default__(self, data, children, meta):
        if data == 'or':
            return children[0] or children[1]
        if data == 'and':
            return children[0] and children[1]
        if data == 'not':
            return not children[0]
        if data == 'start':
            return children[0]
        raise Exception('unexpected tree node "{data}", {children}, {meta}')
    
    def TAG(self, tk: lark.Token):
        return tk.value in self.tags

def match_tags(tree: lark.Tree, tags: Set[int]) -> bool:
    return Matcher(tags).transform(tree)
