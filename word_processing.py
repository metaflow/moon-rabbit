#!/usr/bin/python
#
# Copyright 2021 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Analyze words"""

from io import StringIO
from data import *
from twitchio.ext import commands as twitchCommands  # type: ignore
ttldict2  # type: ignore
from storage import DB, db, set_db
from typing import Callable, List, Set, Union
import pymorphy2  # type: ignore
from query import query_parser

morph = pymorphy2.MorphAnalyzer(lang='ru')


def inflect(line: str, inf: str, tagFilter: List[str] = [], n: Optional[int] = None) -> str:
    if not args:
        return line
    inf = morph.cyr2lat(inf)
    ss: Set[str] = set(inf.split(','))
    parts = re.split(r'(\s+)', line.strip())
    for i in range(0, len(parts), 2):
        mm = morph.parse(parts[i])
        j = i // 2
        if len(tagFilter) > j:
            if not tagFilter[j]:
                continue
            for or_match in tagFilter[j].split(';'):
                tf = morph.cyr2lat(or_match).split(',')
                mm = [x for x in mm if any((p in x.tag) for p in tf)]
        if not mm:
            continue
        t = mm[0]
        if 'NOUN' in t.tag:
            if ss:
                x = t.inflect(ss)
                if x:
                    t = x
            if n:
                x = t.make_agree_with_number(n)
                if x:
                    t = x
        else:
            if n:
                x = t.make_agree_with_number(n)
                if x:
                    t = x
            if ss:
                x = t.inflect(ss)
                if x:
                    t = x
        parts[i] = t.word
    return ''.join(parts)

if __name__ == "__main__":
    cases = ['gent', 'datv', 'accs', 'ablt', 'loct']
    with open(sys.argv[1], encoding='utf-8') as f, open(sys.argv[2], encoding='utf-8', mode='wt') as fw:
        for line in f:
            s = line.strip()
            mm = morph.parse(s)
            masc = [x for x in mm if ('masc' in x.tag or 'ms-f' in x.tag)]
            fw.write(s + '\t')
            if not masc:
                fw.write('no masc' + str(mm) + '\n')
                continue
            p = masc[0]
            fw.write('\t'.join([p.inflect({c}).word for c in cases]) + '\n')
