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
from storage import DB, db, set_db
from typing import Callable, List, Set, Union
import pymorphy2  # type: ignore
from query import query_parser
import sys
from words import morph

if __name__ == "__main__":
    cases = ['nomn', 'gent', 'datv', 'accs', 'ablt', 'loct']
    with open(sys.argv[1], encoding='utf-8') as f, open(sys.argv[2], encoding='utf-8', mode='at') as fw:
        for line in f:
            row = []
            s, manual_tags = line.strip().split('\t')
            mm = morph.parse(s)
            masc = [x for x in mm if ('masc' in x.tag or 'ms-f' in x.tag)]
            row.append(s)
            suggested = []
            ii = []
            for p in morph.parse(s):
                tags = list(p.tag.grammemes)
                if 'nomn' not in tags:
                    continue
                if ('ADJF' in tags) or ('ADJS' in tags) or ('PRTF' in tags) or ('PRTS' in tags):
                    tags.append('FEAT')
                if ('ms-f' in tags):
                    tags.append('masc')
                    tags.append('femn')
                tags = ["_" + x for x in tags if x != 'nomn']
                tags.append("morph")
                logging.info(f'morph parse {p} {p.tag.grammemes} {tags}')
                suggested.append(' '.join(tags))
                inf = []
                for c in cases:
                    x = p.inflect({c})
                    if not x:
                        inf.append('X')
                    else:
                        inf.append(x.word)
                for c in cases:
                    x = p.inflect({c, 'plur'})
                    if not x:
                        inf.append('X')
                    else:
                        inf.append(x.word)
                ii.append(','.join(inf))
            if suggested:
                row.append(manual_tags + ' ' + suggested[0])
                row.append(manual_tags)
                row.append('"' + '\n'.join(suggested) + '"')
                row.append('"' + '\n'.join(ii) + '"')
            else:
                row.append(manual_tags)
                row.append('')
                row.append('')
                row.append('')
            fw.write('\t'.join(row) + '\n')
            # fw.write('\t'.join([p.inflect({c}).word for c in cases]) + '\n')
