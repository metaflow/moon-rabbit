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

import csv
import io
import logging
import re
import urllib.request

import discord

import query
import words
from commands.pipeline import Command, command_prefix
from data import Action, ActionKind, Message, str_to_int
from storage import db


def str_to_tags(s: str) -> tuple[dict[str, str | None], bool]:
    z: dict[str, str | None] = {}
    if s.strip() == "":
        return (z, True)
    for line in s.split("\n"):
        x = line.strip()
        if not x:
            continue
        parts = x.split("=", 1)
        value: str | None = None
        name = parts[0].strip()
        if not query.good_tag_name(name):
            logging.warning(f'tag "{name}" is invalid')
            return (z, False)
        if len(parts) > 1:
            value = parts[1].strip()
        z[name] = value
    return (z, True)


def tag_values_to_str(tags: dict[str, str | None]) -> str:
    z = []
    for n, v in tags.items():
        if not v:
            z.append(n)
            continue
        z.append(f"{n}={v}")
    return "\n".join(z)


def text_to_row(channel_id: int, text_id: int) -> str | None:
    text_value = db().get_text(channel_id, text_id)
    if not text_value:
        return None
    tags = db().get_text_tag_values(channel_id, text_id)
    tag_by_id = db().tag_by_id(channel_id)
    name_value: dict[str, str | None] = {}
    for id, value in tags.items():
        name = tag_by_id[id]
        name_value[name] = value
    sbuf = io.StringIO()
    csv.writer(sbuf, delimiter=";").writerow([text_value, text_id, tag_values_to_str(name_value)])
    return sbuf.getvalue().strip()


def morph_text(text_value: str) -> dict[str, str | None]:
    name_value: dict[str, str | None] = {}
    parts = re.split(r"(\s+)", text_value.strip())
    ww = [parts[i] for i in range(0, len(parts), 2)]
    parses = [words.morph.parse(w) for w in ww]
    for i in range(len(parses)):
        parses[i] = [p for p in parses[i] if "nomn" in list(p.tag.grammemes)]
    logging.debug(f"morph parses for parts {parses}")
    if any(parses):
        for inf in words.case_tags:
            ss = set(words.morph.cyr2lat(inf).split(","))
            t = ""
            for i in range(len(parts)):
                j = i // 2
                if i % 2 != 0 or not parses[j]:
                    t += parts[i]
                    continue
                x = parses[j][0].inflect(ss)
                if not x:
                    t += parts[i]
                else:
                    t += x.word
            name_value[inf] = t
    return name_value


def import_text_row(
    channel_id: int, row: list[str], tag_by_name: dict[str, int]
) -> tuple[int, int, int]:
    updated = 0
    added = 0
    if not row:
        return (updated, added, 0)
    txt = row[0].strip()
    if not txt:
        return (updated, added, 0)
    text_id = 0
    if len(row) >= 2:
        s = row[1].strip()
        if s:
            text_id = str_to_int(s)
            if not text_id:
                logging.warning(f'failed to convert "{s}" to number')
                return (0, 0, 0)
    tags_info: dict[str, str | None] | None = None
    if len(row) >= 3:
        tags_info, ok = str_to_tags(row[2])
        if not ok:
            logging.warning(f'failed to get tags from "{row[2]}"')
            return (0, 0, 0)
    if text_id:
        if db().set_text(channel_id, txt, text_id):
            updated += 1
        else:
            return (0, 0, 0)
    else:
        existing = db().find_text(channel_id, txt)
        if existing:
            text_id = existing
            updated += 1
        else:
            text_id = db().add_text(channel_id, txt)
            added += 1
    if tags_info:
        tag_values: dict[int, str | None] = {}
        if logging.getLogger().isEnabledFor(logging.DEBUG):
            logging.debug(f"#{text_id} {txt} tags dict {tags_info}")
        for name, value in tags_info.items():
            if name not in tag_by_name:
                db().add_tag(channel_id, name)
                tag_by_name = db().tag_by_value(channel_id)
            tag_values[tag_by_name[name]] = value
        db().set_text_tags(channel_id, text_id, tag_values)
    return (updated, added, text_id)


class TagDelete(Command):
    def run(self, msg: Message) -> tuple[list[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ["tag-rm"])
        if not text:
            return [], True
        text = text.strip()
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help_full(msg.prefix))], False
        channel_id = msg.channel_id
        tag_id: int
        tag_id = str_to_int(text) if text.isdigit() else db().tag_by_value(channel_id)[text]
        deleted = db().delete_tag(channel_id, tag_id)
        return [
            Action(
                kind=ActionKind.REPLY,
                text=(f"removed tag {text}" if deleted == 1 else "no such tag"),
            )
        ], False

    def help(self, prefix: str):
        return f"{prefix}tag-rm"

    def help_full(self, prefix: str):
        return f"{prefix}tag-rm <tag id>"


class TagList(Command):
    def run(self, msg: Message) -> tuple[list[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ["tags"])
        if not text:
            return [], True
        channel_id = msg.channel_id
        tags = db().tag_by_value(channel_id)
        s = "no tags"
        if tags:
            t: list[str] = []
            for tag in sorted(tags):
                t.append(f"{tags[tag]} {tag}")
            s = "\n".join(t)
        return [Action(kind=ActionKind.REPLY, text=s)], False

    def help(self, prefix: str):
        return f"{prefix}tags"


class TextSet(Command):
    def run(self, msg: Message) -> tuple[list[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ["add"])
        if not text:
            return [], True
        text = text.strip()
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help_full(msg.prefix))], False
        row = list(csv.reader(io.StringIO(text), delimiter=";"))[0]
        channel_id = msg.channel_id
        prev: str | None = None
        if row:
            if len(row) >= 2 and row[1].isdigit():
                prev = text_to_row(channel_id, str_to_int(row[1]))
            else:
                text_id = db().find_text(channel_id, row[0].strip())
                if text_id:
                    prev = text_to_row(channel_id, text_id)
        updated, _, text_id = import_text_row(channel_id, row, db().tag_by_value(channel_id))
        if text_id:
            s = f"{'Updated' if updated else 'Added'} text (text;id;tags):\n{text_to_row(channel_id, text_id)}"
            if prev:
                s += f"\nPrevious value:\n{prev}"
        else:
            raise Exception("failed to insert new text")
        return [Action(kind=ActionKind.REPLY, text=s)], False

    def help(self, prefix: str):
        return f"{prefix}add"

    def help_full(self, prefix: str):
        return f'{prefix}add "some text";id;"tag1<newline>tag2<newline>tag3..."'


class TextDescribe(Command):
    def run(self, msg: Message) -> tuple[list[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ["describe"])
        if not text:
            return [], True
        text = text.strip()
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help_full(msg.prefix))], False
        channel_id = msg.channel_id
        if not text.isdigit():
            return [Action(kind=ActionKind.REPLY, text="Please provide a text id")], False
        s = text_to_row(channel_id, str_to_int(text))
        if not s:
            return [Action(kind=ActionKind.REPLY, text=f"Text #{text} is not found")], False
        return [Action(kind=ActionKind.REPLY, text=f"{msg.prefix}add {s}")], False

    def help(self, prefix: str):
        return f"{prefix}describe"

    def help_full(self, prefix: str):
        return f"{prefix}describe <text id>\nPrint full info about text."


class TextNew(Command):
    def run(self, msg: Message) -> tuple[list[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ["new"])
        if not text:
            return [], True
        text = text.strip()
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help_full(msg.prefix))], False
        tt = text.strip().split(";", 1)
        text_value = tt[0].strip()
        name_value: dict[str, str | None] = morph_text(text_value)
        if len(tt) >= 2:
            for s in tt[1].split(" "):
                if query.good_tag_name(s):
                    name_value[s] = None
        str_buf = io.StringIO()
        csv.writer(str_buf, delimiter=";").writerow([text_value, "", tag_values_to_str(name_value)])
        return [Action(kind=ActionKind.REPLY, text=f"{msg.prefix}add {str_buf.getvalue()}")], False

    def help(self, prefix: str):
        return f"{prefix}new"

    def help_full(self, prefix: str):
        return f'{prefix}new <text>;tag1 tag2 tag3\nTries to morphologically analyze text and prints new "add" command.'


class TextSetNew(Command):
    def run(self, msg: Message) -> tuple[list[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ["setnew"])
        if not text:
            return [], True
        text = text.strip()
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help_full(msg.prefix))], False
        tt = text.strip().split(";", 1)
        text_value = tt[0].strip()
        name_value: dict[str, str | None] = morph_text(text_value)
        if len(tt) >= 2:
            for s in tt[1].split(" "):
                if query.good_tag_name(s):
                    name_value[s] = None
        channel_id = msg.channel_id
        updated, _, text_id = import_text_row(
            channel_id,
            [text_value, "", tag_values_to_str(name_value)],
            db().tag_by_value(channel_id),
        )
        if text_id:
            s = f"{'Updated' if updated else 'Added'} text (text;id;tags):\n{text_to_row(channel_id, text_id)}"
        else:
            raise Exception("failed to insert new text")
        return [Action(kind=ActionKind.REPLY, text=s)], False

    def help(self, prefix: str):
        return f"{prefix}setnew"

    def help_full(self, prefix: str):
        return f"{prefix}setnew <text>;tag1 tag2 tag3\nSame as running command returned by !new."


class TextUpload(Command):
    def run(self, msg: Message) -> tuple[list[Action], bool]:
        if msg.txt.strip() != msg.prefix + "upload":
            return [], True
        v = msg.get_variables()
        log = msg.log
        content = ""
        discord_msg: discord.Message = v["_discord_message"]
        log.info("looking for attachments")
        for att in discord_msg.attachments:
            log.info(f"attachment {att.filename} {att.size} {att.content_type}")
            req = urllib.request.Request(att.url, headers={"User-Agent": "Mozilla/5.0"})
            content = urllib.request.urlopen(req).read().decode("utf-8")
            break
        channel_id = msg.channel_id
        all_tags: set[str] = set()
        for row in csv.reader(io.StringIO(content)):
            if len(row) < 3:
                continue
            tags, ok = str_to_tags(row[2])
            if ok:
                all_tags.update(tags.keys())
        logging.debug(f"all tags in file: {all_tags}")
        for t in all_tags:
            db().add_tag(channel_id, t)
        tag_by_name = db().tag_by_value(channel_id)
        total = 0
        total_added = 0
        total_updated = 0
        bad_rows = []
        i = 0
        for row in csv.reader(io.StringIO(content)):
            i += 1
            if not row:
                continue
            updated, added, text_id = import_text_row(channel_id, row, tag_by_name)
            if not text_id:
                bad_rows.append(i)
            total_updated += updated
            total_added += added
            total += 1
        s = f"Added {total_added} and updated {total_updated} texts from non-empty {total} row with tags {all_tags}."
        if bad_rows:
            s += f"\nBad rows numbers: {','.join([str(i) for i in bad_rows])}"
        return [Action(kind=ActionKind.REPLY, text=s)], False

    def help(self, prefix: str):
        return f"{prefix}upload"

    def for_twitch(self):
        return False


class TextDownload(Command):
    def run(self, msg: Message) -> tuple[list[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ["download"])
        if not text:
            return [], True
        text = text.strip()
        channel_id = msg.channel_id
        items: list[tuple[int, str, set[int]]] = []
        if not text:
            items = db().all_texts(channel_id)
        else:
            query_parts = text.split(";", 1)
            substring = query_parts[0]
            tag_query = ""
            if len(query_parts) > 1:
                tag_query = query_parts[1]
            items = db().text_search(channel_id, substring, tag_query)
        if not items:
            return [Action(kind=ActionKind.REPLY, text="no results")], False
        tag_by_id = db().tag_by_id(channel_id)
        output = io.StringIO()
        writer = csv.writer(output)
        for ii in items:
            text_id = ii[0]
            txt = ii[1]
            tags = db().get_text_tag_values(channel_id, text_id)
            name_value: dict[str, str | None] = {}
            for id, value in tags.items():
                name = tag_by_id[id]
                # TODO: remove when morph tag is migrated to tag values
                if name == "morph":
                    filter = []
                    if tags:
                        for tag_id in tags:
                            name = tag_by_id[tag_id]
                            if name in words.morph_tags:
                                filter.append(words.morph_tags[name])
                    for inf in words.case_tags:
                        name_value[inf] = words.inflect_word(txt, inf, filter)
                    continue
                name_value[name] = value
            writer.writerow([txt, text_id, tag_values_to_str(name_value)])
        return [
            Action(
                kind=ActionKind.REPLY,
                text="texts",
                attachment=output.getvalue(),
                attachment_name="texts.csv",
            )
        ], False

    def for_twitch(self):
        return False

    def help(self, prefix: str):
        return f"{prefix}download"

    def help_full(self, prefix: str):
        return f"{prefix}download [<substring>[;<tag query>]]"


class TextSearch(Command):
    def run(self, msg: Message) -> tuple[list[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ["search"])
        if not text:
            return [], True
        text = text.strip()
        channel_id = msg.channel_id
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help(msg.prefix))], False
        query_parts = text.split(";", 1)
        substring = query_parts[0]
        tag_query = ""
        if len(query_parts) > 1:
            tag_query = query_parts[1]
        items = db().text_search(channel_id, substring, tag_query)
        if not items:
            return [Action(kind=ActionKind.REPLY, text="no results")], False
        tag_by_id = db().tag_by_id(channel_id)
        if not items:
            return [Action(kind=ActionKind.REPLY, text="no results")], False
        sbuf = io.StringIO()
        for ii in items:
            tag_names = [tag_by_id[x] for x in ii[2]]
            tag_names = [x for x in tag_names if x not in words.case_tags]
            csv.writer(sbuf, delimiter=";").writerow([ii[1], ii[0], " ".join(tag_names)])
        return [Action(kind=ActionKind.REPLY, text=sbuf.getvalue())], False

    def help(self, prefix: str):
        return f"{prefix}search"

    def help_full(self, prefix: str):
        return f"{prefix}search <substring><tag query>]"


class TextRemove(Command):
    def run(self, msg: Message) -> tuple[list[Action], bool]:
        text = command_prefix(msg.txt, msg.prefix, ["rm"])
        if not text:
            return [], True
        text = text.strip()
        channel_id = msg.channel_id
        if not text:
            return [Action(kind=ActionKind.REPLY, text=self.help(msg.prefix))], False
        text_id: int | None
        if text.isdigit():
            text_id = str_to_int(text)
        else:
            query_parts = text.split(";", 1)
            substring = query_parts[0]
            tag_query = ""
            if len(query_parts) > 1:
                tag_query = query_parts[1]
            items = db().text_search(channel_id, substring, tag_query)
            if not items:
                return [Action(kind=ActionKind.REPLY, text="No matches found")], False
            if len(items) == 1:
                text_id, text, _ = items[0]
            else:
                return [
                    Action(kind=ActionKind.REPLY, text="Multiple matches for that query")
                ], False
        if not text_id:
            return [Action(kind=ActionKind.REPLY, text="No text is found")], False
        s = text_to_row(channel_id, text_id)
        t = db().delete_text(channel_id, text_id)
        if t:
            return [Action(kind=ActionKind.REPLY, text=f"Deleted text\n{s}")], False
        return [Action(kind=ActionKind.REPLY, text=f"Text #{text_id} is not found")], False

    def help(self, prefix: str):
        return f"{prefix}rm"

    def help_full(self, prefix: str):
        return f"{prefix}rm <id|substring;tag query>"
