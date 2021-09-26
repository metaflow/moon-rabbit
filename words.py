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
import pymorphy2
from pymorphy2.analyzer import MorphAnalyzer  # type: ignore

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

def inflect_word(s: str, inf: str, tagFilter: List[str] = [], n: Optional[int] = None) -> str:
    inf = morph.cyr2lat(inf)
    ss = set(inf.split(','))
    logging.info(f'inflecting "{s}" to "{inf}"({ss}) filter={tagFilter}')
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
                logging.info(f'matched parse {x}')
                p = x
                matched = True
                break
            else:
                logging.info(f'unmatched parse {x}')
        if not matched:
            logging.info(f'no matches found for {s} and filter {tagFilter}')
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

def suggest_tags(s: str) -> str:
    suggested = []
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
        suggested.append(' '.join(tags) + '\n' + ', '.join(inf))
    return '\n'.join(suggested)

# def inflect(line: str, inf: str, tagFilter: List[str] = [], n: Optional[int] = None) -> str:
#     inf = morph.cyr2lat(inf)
#     ss: Set[str] = set(inf.split(','))
#     parts = re.split(r'(\s+)', line.strip())
#     for i in range(0, len(parts), 2):
#         mm = morph.parse(parts[i])
#         j = i // 2
#         if len(tagFilter) > j:
#             if not tagFilter[j]:
#                 continue
#             for or_match in tagFilter[j].split(';'):
#                 tf = morph.cyr2lat(or_match).split(',')
#                 mm = [x for x in mm if any((p in x.tag) for p in tf)]
#         if not mm:
#             continue
#         t = mm[0]
#         if 'NOUN' in t.tag:
#             if ss:
#                 x = t.inflect(ss)
#                 if x:
#                     t = x
#             if n:
#                 x = t.make_agree_with_number(n)
#                 if x:
#                     t = x
#         else:
#             if n:
#                 x = t.make_agree_with_number(n)
#                 if x:
#                     t = x
#             if ss:
#                 x = t.inflect(ss)
#                 if x:
#                     t = x
#         parts[i] = t.word
#     return ''.join(parts)