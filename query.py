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

import lark
import re

query_grammar = """
?start: sub
?sub: and | and "not"i and
?and: or | and "+" or | and "and"i or
?or: atom | or "|" atom | or "," atom | or "and"i atom
?atom: NAME | "(" and ")"
NAME: (LETTER|"-"|"_"|DIGIT)+
%import common.LETTER
%import common.DIGIT
%import common.WS
%ignore WS
"""

query_parser = lark.Lark(query_grammar)
tag_re = re.compile('[a-z0-9-_]*', re.IGNORECASE)