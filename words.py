"""
 Copyright 2021  Google LLC

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

import logging
from typing import Dict, List, Optional, Type
import pymorphy2 # type: ignore

# Docs: https://pymorphy2.readthedocs.io/en/latest/user/index.html
morph = pymorphy2.MorphAnalyzer(lang='ru')

morph_tags: Dict[str, str] = {
    '_actv': 'actv',
    '_ADJF': 'ADJF',
    '_Adjx': 'Adjx',
    '_anim': 'anim',
    '_Anum': 'Anum',
    '_Apro': 'Apro',
    '_femn': 'femn',
    '_Fixd': 'Fixd',
    '_impf': 'impf',
    '_inan': 'inan',
    '_Infr': 'Infr',
    '_Inmx': 'Inmx',
    '_intr': 'intr',
    '_masc': 'masc',
    '_ms-f': 'ms-f',
    '_Name': 'Name',
    '_neut': 'neut',
    '_NOUN': 'NOUN',
    '_Orgn': 'Orgn',
    '_past': 'past',
    '_perf': 'perf',
    '_Poss': 'Poss',
    '_pres': 'pres',
    '_PRTF': 'PRTF',
    '_pssv': 'pssv',
    '_Qual': 'Qual',
    '_Sgtm': 'Sgtm',
    '_sing': 'sing',
    '_Slng': 'Slng',
    '_Subx': 'Subx',
    '_Supr': 'Supr',
    '_Surn': 'Surn',
    '_tran': 'tran'
}

cases = ['nomn', 'gent', 'datv', 'accs', 'ablt', 'loct']
case_tags = ['рд','дт','вн','тв','пр','мн','рд,мн','дт,мн','вн,мн','тв,мн','пр,мн']

def inflect_word(s: str, inf: str, tagFilter: List[str] = [], n: Optional[int] = None) -> str:
    inf = morph.cyr2lat(inf)
    ss = set(inf.split(','))
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.debug(f'inflecting "{s}" to "{inf}"({ss}) filter={tagFilter}')
    mm = morph.parse(s)
    if not mm:
        return s
    p: Type[pymorphy2.analyzer.Parse] = mm[0]
    if tagFilter:
        matched = False
        for x in mm:
            match = True
            for t in tagFilter:
                if (t in x.tag) or (t == 'masc' and 'ms-f' in x.tag) or (t == 'femn' and 'ms-f' in x.tag):
                    continue
                match = False
            if match:
                if logging.getLogger().isEnabledFor(logging.DEBUG):
                    logging.debug(f'matched parse {x}')
                p = x
                matched = True
                break
            else:
                if logging.getLogger().isEnabledFor(logging.DEBUG):
                    logging.debug(f'unmatched parse {x}')
        if not matched:
            logging.warn(f'no matches found for {s} and filter {tagFilter}')
            return s
    if 'NOUN' in p.tag:
        if ss:
            x = p.inflect(ss)
            if x:
                p = x
        if n:
            x = p.make_agree_with_number(n)
            if x:
                p = x
    else:
        if n:
            x = p.make_agree_with_number(n)
            if x:
                p = x
        if ss:
            x = p.inflect(ss)
            if x:
                p = x
    return p.word