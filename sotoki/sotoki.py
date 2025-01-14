#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: ai ts=4 sts=4 et sw=4 nu

"""sotoki.

Usage:
  sotoki <domain> <publisher> [--directory=<dir>] [--nozim] [--tag-depth=<tag_depth>] [--threads=<threads>] [--zimpath=<zimpath>] [--optimization-cache=<optimization-cache>] [--reset] [--reset-images] [--clean-previous] [--nofulltextindex] [--ignoreoldsite] [--nopic] [--no-userprofile] [--no-identicons] [--no-externallink] [--no-unansweredquestion]
  sotoki (-h | --help)
  sotoki --version

Options:
  -h --help                                     Display this help
  --version                                     Display the version of Sotoki
  --directory=<dir>                             Configure directory in which XML files will be stored [default: download]
  --nozim                                       Doesn't build a ZIM file, output will be in 'work/output/' in flat HTML files
  --tag-depth=<tag_depth>                       Configure the number of questions, ordered by Score, to display in tags pages (should be a multiple of 100, default all question are in tags pages) [default: -1]
  --threads=<threads>                           Number of threads to use, default is number_of_cores/2
  --zimpath=<zimpath>                           Final path of the zim file
  --reset                                       Reset dump
  --reset-images                                Remove images in cache
  --clean-previous                              Delete only data from a previous run with '--nozim' or which failed
  --nofulltextindex                             Doesn't index content
  --ignoreoldsite                               Ignore Stack Exchange closed sites
  --nopic                                       Doesn't download images
  --no-userprofile                              Doesn't include user profiles
  --no-identicons                               Use generated profile picture only (user images won't be downloaded)
  --no-externallink                             Remove external link
  --no-unansweredquestion                       Doesn't include questions with no answers
  --optimization-cache=<optimization-cache>     Use optimization cache with given URL and credentials. The argument needs to be of the form <endpoint-url>?keyId=<key-id>&secretAccessKey=<secret-access-key>&bucketName=<bucket-name>
"""
import re
import sys
import os
import html
import shlex
import shutil
import requests
import sqlite3
import os.path
import pathlib
import tempfile
import datetime
import subprocess
from hashlib import sha256
from string import punctuation
from docopt import docopt, DocoptExit
from distutils.dir_util import copy_tree
from multiprocessing import cpu_count, Queue, Process
from xml.sax import make_parser, handler
import urllib.request
import urllib.parse
from urllib.request import urlopen
from PIL import Image
import mistune
from mistune.plugins import plugin_url
from slugify import slugify
import bs4 as BeautifulSoup
from jinja2 import Environment
from jinja2 import FileSystemLoader
from lxml import etree
from lxml.html import fromstring as string2html
from lxml.html import tostring as html2string
from kiwixstorage import KiwixStorage
from pif import get_public_ip
from zimscraperlib.download import save_large_file
from zimscraperlib.zim import make_zim_file
from zimscraperlib.filesystem import get_file_mimetype

ROOT_DIR = pathlib.Path(__file__).parent
NAME = ROOT_DIR.name

with open(ROOT_DIR.joinpath("VERSION"), "r") as fh:
    VERSION = fh.read().strip()

SCRAPER = f"{NAME} {VERSION}"

MARKDOWN = None
TMPFS_DIR = "/dev/shm" if os.path.isdir("/dev/shm") else None

CACHE_STORAGE_URL = None

redirect_file = None
output_dir = None


#########################
#        Question       #
#########################
class QuestionRender(handler.ContentHandler):
    def __init__(
        self,
        templates,
        title,
        publisher,
        dump,
        cores,
        cursor,
        conn,
        site_url,
        domain,
        mathjax,
        nopic,
        nouserprofile,
        noexternallink,
        no_unansweredquestion,
    ):
        self.templates = templates
        self.title = title
        self.publisher = publisher
        self.dump = dump
        self.cores = cores
        self.cursor = cursor
        self.conn = conn
        self.site_url = site_url
        self.domain = domain
        self.post = {}
        self.comments = []
        self.answers = []
        self.whatwedo = "post"
        self.nb = 0  # Nomber of post generate
        os.makedirs(os.path.join(output_dir, "question"))
        self.request_queue = Queue(cores * 2)
        self.workers = []
        self.conn = conn
        self.mathjax = mathjax
        self.nopic = nopic
        self.nouserprofile = nouserprofile
        self.noexternallink = noexternallink
        self.no_unansweredquestion = no_unansweredquestion
        for i in range(self.cores):
            self.workers.append(Worker(self.request_queue))
        for i in self.workers:
            i.start()

    def startElement(self, name, attrs):  # For each element
        if (
            name == "comments" and self.whatwedo == "post"
        ):  # We match if it's a comment of post
            self.whatwedo = "post/comments"
            self.comments = []
            return
        if name == "comments" and self.whatwedo == "post/answers":  # comment of answer
            self.whatwedo = "post/answers/comments"
            self.comments = []
            return
        if name == "answers":  # a answer
            self.whatwedo = "post/answers"
            self.comments = []
            self.answers = []
            return
        if name == "row":  # Here is a answer
            tmp = {}
            for k in list(attrs.keys()):  # Get all item
                tmp[k] = attrs[k]
            tmp["Score"] = int(tmp["Score"])
            if (
                "AcceptedAnswerId" in self.post
                and self.post["AcceptedAnswerId"] == tmp["Id"]
            ):
                tmp["Accepted"] = True
            else:
                tmp["Accepted"] = False

            if (
                "OwnerUserId" in tmp
            ):  # We put the good name of the user how made the post
                user = self.cursor.execute(
                    "SELECT * FROM users WHERE id = ?", (int(tmp["OwnerUserId"]),)
                ).fetchone()
                oid = tmp["OwnerUserId"]
                if user is not None:
                    tmp["OwnerUserId"] = dict_to_unicodedict(user)
                    tmp["OwnerUserId"]["Id"] = oid
                    if self.nouserprofile:
                        tmp["OwnerUserId"]["Path"] = None
                    else:
                        tmp["OwnerUserId"]["Path"] = page_url(
                            tmp["OwnerUserId"]["Id"], tmp["OwnerUserId"]["DisplayName"]
                        )
                else:
                    tmp["OwnerUserId"] = dict_to_unicodedict({"DisplayName": "None"})
                    tmp["OwnerUserId"]["Id"] = oid
            elif "OwnerDisplayName" in tmp:
                tmp["OwnerUserId"] = dict_to_unicodedict(
                    {"DisplayName": tmp["OwnerDisplayName"]}
                )
            else:
                tmp["OwnerUserId"] = dict_to_unicodedict({"DisplayName": "None"})
            # print "        new answers"
            self.answers.append(tmp)
            return

        if name == "comment":  # Here is a comments
            tmp = {}
            for k in list(attrs.keys()):  # Get all item
                tmp[k] = attrs[k]
            # print "                 new comments"
            if "UserId" in tmp:  # We put the good name of the user how made the comment
                user = self.cursor.execute(
                    "SELECT * FROM users WHERE id = ?", (int(tmp["UserId"]),)
                ).fetchone()
                if "UserId" in tmp and user is not None:
                    tmp["UserDisplayName"] = dict_to_unicodedict(user)["DisplayName"]
                    if self.nouserprofile:
                        tmp["Path"] = None
                    else:
                        tmp["Path"] = page_url(tmp["UserId"], tmp["UserDisplayName"])
                else:
                    tmp["UserDisplayName"] = "None"
            else:
                tmp["UserDisplayName"] = "None"

            if "Score" in tmp:
                tmp["Score"] = int(tmp["Score"])
            tmp["Text"] = markdown(tmp["Text"])
            self.comments.append(tmp)
            return

        if name == "link":  # We add link
            if attrs["LinkTypeId"] == "1":
                self.post["relateds"].append(
                    {
                        "PostId": str(attrs["PostId"]),
                        "PostName": html.escape(attrs["PostName"], quote=False),
                    }
                )
            elif attrs["LinkTypeId"] == "3":
                self.post["duplicate"].append(
                    {
                        "PostId": str(attrs["PostId"]),
                        "PostName": html.escape(attrs["PostName"], quote=False),
                    }
                )
            return

        if (
            name != "post"
        ):  # We go out if it's not a post, we because we have see all name of posible tag (answers, row,comments,comment and we will see after post) This normally match only this root
            print("nothing " + name)
            return

        if name == "post":  # Here is a post
            self.whatwedo = "post"
            for k in list(attrs.keys()):  # get all item
                self.post[k] = attrs[k]
            self.post["relateds"] = []  # Prepare list for relateds question
            self.post["duplicate"] = []  # Prepare list for duplicate question
            self.post["filename"] = self.post["Id"] + ".html"

            if (
                "OwnerUserId" in self.post
            ):  # We put the good name of the user how made the post
                user = self.cursor.execute(
                    "SELECT * FROM users WHERE id = ?", (int(self.post["OwnerUserId"]),)
                ).fetchone()
                oid = self.post["OwnerUserId"]
                if user is not None:
                    self.post["OwnerUserId"] = dict_to_unicodedict(user)
                    self.post["OwnerUserId"]["Id"] = oid
                    if self.nouserprofile:
                        self.post["OwnerUserId"]["Path"] = None
                    else:
                        self.post["OwnerUserId"]["Path"] = page_url(
                            self.post["OwnerUserId"]["Id"],
                            self.post["OwnerUserId"]["DisplayName"],
                        )
                else:
                    self.post["OwnerUserId"] = dict_to_unicodedict(
                        {"DisplayName": "None"}
                    )
                    self.post["OwnerUserId"]["Id"] = oid
            elif "OwnerDisplayName" in self.post:
                self.post["OwnerUserId"] = dict_to_unicodedict(
                    {"DisplayName": self.post["OwnerDisplayName"]}
                )
            else:
                self.post["OwnerUserId"] = dict_to_unicodedict({"DisplayName": "None"})

    def endElement(self, name):
        if (
            self.whatwedo == "post/answers/comments"
        ):  # If we have a post with answer and comment on this answer, we put comment into the anwer
            self.answers[-1]["comments"] = self.comments
            self.whatwedo = "post/answers"
        if (
            self.whatwedo == "post/answers"
        ):  # If we have a post with answer(s), we put answer(s) we put them into post
            self.post["answers"] = self.answers
        elif (
            self.whatwedo == "post/comments"
        ):  # If we have post without answer but with comments we put comment into post
            self.post["comments"] = self.comments

        if name == "post":
            if self.no_unansweredquestion and self.answers == [] :
                # Reset element
                self.post = {}
                self.comments = []
                self.answers = []
                return
            # print self.post
            self.nb += 1
            if self.nb % 1000 == 0:
                print("Already " + str(self.nb) + " questions done!")
                self.conn.commit()
            self.post["Tags"] = self.post["Tags"][1:-1].split("><")
            for t in self.post["Tags"]:  # We put tags into db
                sql = "INSERT INTO QuestionTag(Score, Title, QId, CreationDate, Tag) VALUES(?, ?, ?, ?, ?)"
                self.cursor.execute(
                    sql,
                    (
                        self.post["Score"],
                        self.post["Title"],
                        self.post["Id"],
                        self.post["CreationDate"],
                        t,
                    ),
                )
            # Make redirection
            for ans in self.answers:
                with open(redirect_file, "a") as f_redirect:
                    f_redirect.write(
                        "A\telement/"
                        + str(ans["Id"])
                        + "\tAnswer "
                        + str(ans["Id"])
                        + "\tA/question/"
                        + self.post["Id"]
                        + ".html"
                        + "\n"
                    )
            with open(redirect_file, "a") as f_redirect:
                f_redirect.write(
                    "A\telement/"
                    + str(self.post["Id"])
                    + "\tQuestion "
                    + str(self.post["Id"])
                    + "\tA/question/"
                    + self.post["Id"]
                    + ".html"
                    + "\n"
                )

            data_send = [
                some_questions,
                self.templates,
                self.title,
                self.publisher,
                self.post,
                "question.html",
                self.site_url,
                self.domain,
                self.mathjax,
                self.nopic,
                self.nouserprofile,
                self.noexternallink,
            ]
            self.request_queue.put(data_send)
            # some_questions(self.templates, self.title, self.publisher, self.post, "question.html", self.site_url, self.domain, self.mathjax, self.nopic)
            # Reset element
            self.post = {}
            self.comments = []
            self.answers = []

    def endDocument(self):
        self.conn.commit()
        # closing thread
        for i in range(self.cores):
            self.request_queue.put(None)
        for i in self.workers:
            i.join()
        print("---END--")


def some_questions(
    templates,
    title,
    publisher,
    question,
    template_name,
    site_url,
    domain,
    mathjax,
    nopic,
    nouserprofile,
    noexternallink,
):
    try:
        question["Score"] = int(question["Score"])
        if "answers" in question:
            question["answers"] = sorted(
                question["answers"], key=lambda k: k["Score"], reverse=True
            )
            question["answers"] = sorted(
                question["answers"], key=lambda k: k["Accepted"], reverse=True
            )  # sorted is stable so accepted will be always first, then other question will be sort in ascending order
            for ans in question["answers"]:
                ans["Body"] = interne_link(ans["Body"], domain, nouserprofile, noexternallink)
                ans["Body"] = image(ans["Body"], nopic)
                if "comments" in ans:
                    for comment in ans["comments"]:
                        comment["Text"] = interne_link(
                            comment["Text"],
                            domain,
                            nouserprofile,
                            noexternallink,
                        )
                        comment["Text"] = image(comment["Text"], nopic)

        filepath = os.path.join(output_dir, "question", question["filename"])
        question["Body"] = interne_link(question["Body"], domain, nouserprofile, noexternallink)
        question["Body"] = image(question["Body"], nopic)
        if "comments" in question:
            for comment in question["comments"]:
                comment["Text"] = interne_link(comment["Text"], domain, nouserprofile, noexternallink)
                comment["Text"] = image(comment["Text"], nopic)
        question["Title"] = html.escape(question["Title"], quote=False)
        try:
            jinja(
                filepath,
                template_name,
                templates,
                False,
                question=question,
                rooturl="..",
                title=title,
                publisher=publisher,
                site_url=site_url,
                mathjax=mathjax,
                nopic=nopic,
            )
        except Exception as e:
            print("Failed to generate %s" % filepath)
            print("Error with jinja" + str(e))
            print(question)
    except Exception as e:
        print("Error with a post : " + str(e))


#########################
#        Tags           #
#########################


class TagsRender(handler.ContentHandler):
    def __init__(
        self,
        templates,
        title,
        publisher,
        dump,
        cores,
        cursor,
        conn,
        tag_depth,
        description,
        mathjax,
    ):
        # index page
        self.templates = templates
        self.title = title
        self.publisher = publisher
        self.dump = dump
        self.cores = cores
        self.cursor = cursor
        self.conn = conn
        self.description = description
        self.tag_depth = tag_depth
        self.mathjax = mathjax
        self.tags = []
        sql = "CREATE INDEX index_tag ON questiontag (Tag)"
        self.cursor.execute(sql)

    def startElement(self, name, attrs):  # For each element
        if name == "row":  # If it's a tag (row in tags.xml)
            if attrs["Count"] != "0":
                self.tags.append(
                    {
                        "TagUrl": urllib.parse.quote(attrs["TagName"]),
                        "TagName": attrs["TagName"],
                        "nb_post": int(attrs["Count"]),
                    }
                )

    def endDocument(self):
        sql = "SELECT * FROM questiontag ORDER BY Score DESC LIMIT 400"
        questions = self.cursor.execute(sql)
        some_questions = questions.fetchmany(400)
        new_questions = []
        questionsids = []
        for question in some_questions:
            question["filepath"] = str(question["QId"]) + ".html"
            question["Title"] = html.escape(question["Title"], quote=False)
            if question["QId"] not in questionsids:
                questionsids.append(question["QId"])
                new_questions.append(question)
        jinja(
            os.path.join(output_dir, "index.html"),
            "index.html",
            self.templates,
            False,
            tags=sorted(self.tags[:200], key=lambda k: k["nb_post"], reverse=True),
            rooturl=".",
            questions=new_questions[:50],
            description=self.description,
            title=self.title,
            publisher=self.publisher,
            mathjax=self.mathjax,
        )
        jinja(
            os.path.join(output_dir, "alltags.html"),
            "alltags.html",
            self.templates,
            False,
            tags=sorted(self.tags, key=lambda k: k["nb_post"], reverse=True),
            rooturl=".",
            title=self.title,
            publisher=self.publisher,
            mathjax=self.mathjax,
        )
        # tag page
        print("Render tag page")
        list_tag = [d["TagName"] for d in self.tags]
        os.makedirs(os.path.join(output_dir, "tag"))
        for tag in list(set(list_tag)):
            dirpath = os.path.join(output_dir, "tag")
            tagpath = os.path.join(dirpath, "%s" % tag)
            os.makedirs(tagpath)
            # build page using pagination
            offset = 0
            page = 1
            if self.tag_depth == -1:
                questions = self.cursor.execute(
                    "SELECT * FROM questiontag WHERE Tag = ? ORDER BY Score DESC",
                    (str(tag),),
                )
            else:
                questions = self.cursor.execute(
                    "SELECT * FROM questiontag WHERE Tag = ? ORDER BY Score DESC LIMIT ?",
                    (
                        str(tag),
                        self.tag_depth,
                    ),
                )

            while offset is not None:
                fullpath = os.path.join(tagpath, "%s.html" % page)
                some_questions = questions.fetchmany(100)
                if len(some_questions) != 100:
                    offset = None
                else:
                    offset += len(some_questions)
                some_questions = some_questions[:99]
                for question in some_questions:
                    question["filepath"] = str(question["QId"]) + ".html"
                    question["Title"] = html.escape(question["Title"], quote=False)
                hasprevious = page != 1
                jinja(
                    fullpath,
                    "tag.html",
                    self.templates,
                    False,
                    tag=tag,
                    index=page,
                    questions=some_questions,
                    rooturl="../..",
                    hasnext=bool(offset),
                    next=page + 1,
                    hasprevious=hasprevious,
                    previous=page - 1,
                    title=self.title,
                    publisher=self.publisher,
                    mathjax=self.mathjax,
                )
                page += 1


#########################
#        Users          #
#########################
class UsersRender(handler.ContentHandler):
    def __init__(
        self,
        templates,
        title,
        publisher,
        dump,
        cores,
        cursor,
        conn,
        site_url,
        mathjax,
        nopic,
        no_identicons,
        nouserprofile,
        noexternallink,
        domain,
    ):
        self.identicon_path = os.path.join(output_dir, "static", "identicon")
        self.templates = templates
        self.title = title
        self.publisher = publisher
        self.dump = dump
        self.cores = cores
        self.cursor = cursor
        self.conn = conn
        self.site_url = site_url
        self.mathjax = mathjax
        self.nopic = nopic
        self.no_identicons = no_identicons
        self.nouserprofile = nouserprofile
        self.noexternallink = noexternallink
        self.domain = domain
        self.id = 0
        if not os.path.exists(self.identicon_path):
            os.makedirs(self.identicon_path)
        os.makedirs(os.path.join(output_dir, "user"))
        # Set-up a list of foreground colours (taken from Sigil).
        self.foreground = [
            "rgb(45,79,255)",
            "rgb(254,180,44)",
            "rgb(226,121,234)",
            "rgb(30,179,253)",
            "rgb(232,77,65)",
            "rgb(49,203,115)",
            "rgb(141,69,170)",
        ]
        # Set-up a background colour (taken from Sigil).
        self.background = "rgb(224,224,224)"

        self.request_queue = Queue(cores * 2)
        self.workers = []
        self.user = {}
        for i in range(self.cores):
            self.workers.append(Worker(self.request_queue))
        for i in self.workers:
            i.start()

    def startElement(self, name, attrs):  # For each element
        if name == "badges":
            self.user["badges"] = {}
        if name == "badge":
            tmp = {}
            for k in list(attrs.keys()):
                tmp[k] = attrs[k]
            if tmp["Name"] in self.user["badges"]:
                self.user["badges"][tmp["Name"]] = self.user["badges"][tmp["Name"]] + 1
            else:
                self.user["badges"][tmp["Name"]] = 1
        if name == "row":
            self.id += 1
            if self.id % 1000 == 0:
                print("Already " + str(self.id) + " Users done !")
                self.conn.commit()
            self.user = {}
            for k in list(attrs.keys()):  # get all item
                self.user[k] = attrs[k]

    def endElement(self, name):
        if name == "row":
            user = self.user
            sql = "INSERT INTO users(id, DisplayName, Reputation) VALUES(?, ?, ?)"
            self.cursor.execute(
                sql, (int(user["Id"]), user["DisplayName"], user["Reputation"])
            )
            if not self.nouserprofile:
                with open(redirect_file, "a") as f_redirect:
                    f_redirect.write(
                        "A\tuser/"
                        + page_url(user["Id"], user["DisplayName"])
                        + "\tUser "
                        + slugify(user["DisplayName"])
                        + "\tA/user/"
                        + user["Id"]
                        + "\n"
                    )
            data_send = [
                some_user,
                user,
                self.templates,
                self.publisher,
                self.site_url,
                self.title,
                self.mathjax,
                self.nopic,
                self.no_identicons,
                self.nouserprofile,
                self.noexternallink,
                self.domain,
            ]
            self.request_queue.put(data_send)
            # some_user(user, self.generator, self.templates, self.publisher, self.site_url, self.title, self.mathjax, self.nopic, self.nouserprofile, self.domain)

    def endDocument(self):
        self.conn.commit()
        # closing thread
        for i in range(self.cores):
            self.request_queue.put(None)
        for i in self.workers:
            i.join()
        print("---END--")


def some_user(
    user,
    templates,
    publisher,
    site_url,
    title,
    mathjax,
    nopic,
    no_identicons,
    nouserprofile,
    noexternallink,
    domain,
):
    filename = user["Id"] + ".png"
    fullpath = os.path.join(output_dir, "static", "identicon", filename)
    if (
        not nopic
        and "ProfileImageUrl" in user
        and not os.path.exists(fullpath)
        and not no_identicons
    ):
        try:
            download_image(
                user["ProfileImageUrl"],
                fullpath,
                convert_png=True,
                resize=128,
            )
        except Exception as exc:
            print(user["ProfileImageUrl"] + " > Failed to download\n" + str(exc) + "\n")

    #
    if not nouserprofile:
        if "AboutMe" in user:
            user["AboutMe"] = interne_link(
                "<p>" + user["AboutMe"] + "</p>", domain, nouserprofile, noexternallink
            )
            user["AboutMe"] = image(user["AboutMe"], nopic)
        # generate user profile page
        filename = user["Id"]
        fullpath = os.path.join(output_dir, "user", filename)
        jinja(
            fullpath,
            "user.html",
            templates,
            False,
            user=user,
            title=title,
            rooturl="..",
            publisher=publisher,
            site_url=site_url,
            mathjax=mathjax,
            nopic=nopic,
        )


#########################
#        Tools          #
#########################


class Worker(Process):
    def __init__(self, queue):
        super(Worker, self).__init__()
        self.queue = queue

    def run(self):
        for data in iter(self.queue.get, None):
            try:
                data[0](*data[1:])
                # some_questions(*data)
            except Exception as exc:
                print("error while rendering :", data)
                print(exc)


def intspace(value):
    orig = str(value)
    new = re.sub(r"^(-?\d+)(\d{3})", r"\g<1> \g<2>", orig)
    if orig == new:
        return new
    return intspace(new)


def markdown(text):
    text_html = MARKDOWN(text)[3:-5]
    if len(text_html) == 0:
        return "-"
    return text_html


def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


def scale(number):
    """Convert number to scale to be used in style to color arrows
    and comment score"""
    number = int(number)
    if number < 0:
        return "negative"
    if number == 0:
        return "zero"
    if number < 3:
        return "positive"
    if number < 8:
        return "good"
    return "verygood"


def page_url(ident, name):
    return str(ident) + "/" + slugify(name)


ENV = None  # Jinja environment singleton


def jinja(output, template, templates, raw, **context):
    template = ENV.get_template(template)
    page = template.render(**context)
    if raw:
        page = "{% raw %}" + page + "{% endraw %}"
    with open(output, "w") as f:
        f.write(page)


def jinja_init(templates):
    global ENV
    templates = os.path.abspath(templates)
    ENV = Environment(loader=FileSystemLoader((templates,)))
    filters = dict(
        markdown=markdown,
        intspace=intspace,
        scale=scale,
        clean=lambda y: [x for x in y if x not in punctuation],
        slugify=slugify,
    )
    ENV.filters.update(filters)


def get_tempfile(suffix):
    return tempfile.NamedTemporaryFile(suffix=suffix, dir=TMPFS_DIR, delete=False).name


def get_filetype(path):
    ftype = "none"
    mime = get_file_mimetype(pathlib.Path(path))
    if "png" in mime:
        ftype = "png"
    elif "jpeg" in mime:
        ftype = "jpeg"
    elif "gif" in mime:
        ftype = "gif"
    elif "ico" in mime:
        ftype = "ico"
    elif "svg+xml" in mime:
        ftype = "svg"
    elif "tiff" in mime:
        ftype = "tiff"
    elif "bmp" in mime:
        ftype = "bmp"
    return ftype


def download_from_cache(key, output, meta_tag, meta_val):
    cache_storage = KiwixStorage(CACHE_STORAGE_URL)
    if cache_storage.has_object_matching_meta(key, meta_tag, meta_val):
        try:
            print(os.path.basename(output) + " > Downloading from cache")
            cache_storage.download_file(key, output, progress=False)
            print(os.path.basename(output) + " > Successfully downloaded from cache")
            return True
        except Exception as e:
            print(
                os.path.basename(output)
                + " > Failed to download from cache\n"
                + str(e)
                + "\n"
            )
            return False
    print(os.path.basename(output) + " > Not found in cache")
    return False


def upload_to_cache(fpath, key, meta_tag, meta_val):
    cache_storage = KiwixStorage(CACHE_STORAGE_URL)
    try:
        cache_storage.upload_file(fpath, key, meta={meta_tag: meta_val})
        print(os.path.basename(fpath) + " > Successfully uploaded to cache")
    except Exception as e:
        raise Exception(
            os.path.basename(fpath) + " > Failed to upload to cache\n" + str(e)
        )


def get_response_headers(url):
    for attempt in range(5):
        try:
            return requests.head(url=url, allow_redirects=True, timeout=30).headers
        except requests.exceptions.Timeout:
            print(f"{url} > HEAD request timed out ({attempt})")
    raise Exception("Max retries exceeded")


def get_meta_from_url(url):
    try:
        response_headers = get_response_headers(url)
    except Exception as exc:
        print(f"{url} > Problem with head request\n{exc}\n")
        return None, None
    else:
        if response_headers.get("etag") is not None:
            return "etag", response_headers["etag"]
        if response_headers.get("last-modified") is not None:
            return "last-modified", response_headers["last-modified"]
        if response_headers.get("content-length") is not None:
            return "content-length", response_headers["content-length"]
    return "default", "default"


def get_image_shortcuts(url, convert_png, resize, ext):
    """(skip, redirect_to) shortcuts for an image if possible

    skip (bool): whether this image is meaningless and should not be downloaded.
    redirect_to: URL of a copy of this image to redirect to"""

    def download_and_return_name(url, source, convert_png, resize, ext):
        """genetare the file name given the parameters, check if present in fs and download if not present
        returns the name of the file which was generated from the params"""

        convertion = "png" if convert_png else "org"
        size = "org" if not resize else str(resize)
        image_path = (
            pathlib.Path(output_dir)
            .joinpath("common_images")
            .joinpath(f"{source}_{convertion}_{size}{ext}")
        )

        # download the duplicate file only once
        if not image_path.exists():
            download_image(
                url=url,
                fullpath=str(image_path),
                convert_png=convert_png,
                resize=resize,
                skip_duplicate_check=True,
            )
        return image_path.name

    parsed_url = urllib.parse.urlparse(url)
    if "gravatar.com" in parsed_url.netloc:
        # parse the url
        url_parts = list(parsed_url)
        query = dict(urllib.parse.parse_qsl(url_parts[4]))

        # check if autogenerated identicon from gravatar
        # for more details see https://en.gravatar.com/site/implement/images/
        if query.get("d", query.get("default", "")) == "identicon" and query.get(
            "f", query.get("forcedefault", "")
        ) in ["y", "1"]:
            return True, None

        # check for mystery-person type identicon
        if query.get("d", query.get("default", "")) == "mp":
            return (
                False,
                download_and_return_name(
                    url, "gravatar_mystery", convert_png, resize, ext
                ),
            )

        # check for blank identicon
        if query.get("d", query.get("default", "")) == "blank":
            return (
                False,
                download_and_return_name(
                    url, "gravatar_blank", convert_png, resize, ext
                ),
            )

        # update query with size = 128 (its the size in which we download identicons)
        query.update({"s": "128"})
        url_parts[4] = urllib.parse.urlencode(query)
        url_with_size = urllib.parse.urlunparse(url_parts)

        # check if potentially a duplicate default image at size = 128
        # these content-length values are the content-length value for
        # the two types of default images, the default1 being the blue 'G' logo
        # and second one being the green autogenerated identicon if hash is invalid
        # these need to be manually changed if the images change but they
        # are here for a pretty long time and shall stay to be so
        # See:
        # - https://www.gravatar.com/avatar/wrong_hash?s=128
        # - https://www.gravatar.com/avatar/XXXXXXXXXXXXXXXXXXXXXXXXX?s=128&d=identicon
        headers = get_response_headers(url_with_size)
        if headers.get("content-length") == "4268":
            return (
                False,
                download_and_return_name(
                    url, "gravatar_default1", convert_png, resize, ext
                ),
            )
        elif headers.get("content-length") == "3505":
            return (
                False,
                download_and_return_name(
                    url, "gravatar_default2", convert_png, resize, ext
                ),
            )

    if "googleusercontent" in parsed_url.netloc:
        headers = get_response_headers(url)

        # custom images have etag whereas autogenerated ones do not (mostly)
        if not headers.get("etag", None):
            content_length = headers.get("content-length")
            image_name_prefix = f"googleusercontent_{content_length}"
            return (
                False,
                download_and_return_name(
                    url, image_name_prefix, convert_png, resize, ".png"
                ),
            )

    if "blend-exchange" in parsed_url.netloc:
        # a specific image is duplicated, whatever the query part
        if parsed_url.path == "/embedImage.png":
            return (
                False,
                download_and_return_name(
                    url, "blend_exchange", convert_png, resize, ext
                ),
            )

    return False, None


def handle_duplicate_images(url, fullpath, convert_png, resize):
    """Whether image is duplicate of existing (and process accordingly)

    Download image file if not (yet) a duplicate
    Write a redirection entry if a duplicate"""

    org_path = pathlib.Path(fullpath)
    skip_download, redirection = get_image_shortcuts(
        url, convert_png, resize, org_path.suffix
    )
    if skip_download:
        # we can generate similar identicon, do download is useless
        return True
    if redirection:
        # got a redirection to a common image
        src_path = str(org_path.relative_to(pathlib.Path(output_dir)))
        dst_path = f"A/common_images/{redirection}"
        print("before redirect write")
        print("redirection")
        with open(redirect_file, "a") as f_redirect:
            f_redirect.write(
                "A\t" + f"{src_path}\t" + "Image Redirection\t" + f"{dst_path}\n"
            )
        print(f"Successfully wrote redirection from {src_path} to {dst_path}")
        return True
    return False


def download_image(
    url, fullpath, convert_png=False, resize=False, skip_duplicate_check=False
):
    downloaded = False
    key = None
    meta_tag = None
    meta_val = None
    if url[0:2] == "//":
        url = "http:" + url
    if not skip_duplicate_check and handle_duplicate_images(
        url, fullpath, convert_png, resize
    ):
        # processed as a potential duplicate
        return
    print(url + " > To be saved as " + os.path.basename(fullpath))
    if CACHE_STORAGE_URL:
        meta_tag, meta_val = get_meta_from_url(url)
        if meta_tag and meta_val:
            src_url = urllib.parse.urlparse(url)
            prefix = f"{src_url.scheme}://{src_url.netloc}/"
            key = f"{src_url.netloc}/{urllib.parse.quote_plus(src_url.geturl()[len(prefix):])}"
            # Key looks similar to ww2.someplace.state.gov/data%2F%C3%A9t%C3%A9%2Fsome+chars%2Fimage.jpeg%3Fv%3D122%26from%3Dxxx%23yes
            downloaded = download_from_cache(key, fullpath, meta_tag, meta_val)
    if not downloaded:
        tmp_img = None
        print(os.path.basename(fullpath) + " > Downloading from URL")
        try:
            tmp_img = get_tempfile(os.path.basename(fullpath))
            save_large_file(url, tmp_img)
            print(os.path.basename(fullpath) + " > Successfully downloaded from URL")
        except subprocess.CalledProcessError as e:
            os.unlink(tmp_img)
            print(
                os.path.basename(fullpath)
                + " > Error while downloading from original URL\n"
                + str(e)
                + "\n"
            )
            raise e
        else:
            ext = get_filetype(tmp_img)
            if ext == "none":
                os.unlink(tmp_img)
                raise Exception(f"{os.path.basename(fullpath)} > Not an image")
            try:
                if convert_png and ext != "png":
                    convert_to_png(tmp_img, ext)
                    ext = "png"
                if resize and ext != "gif":
                    resize_one(tmp_img, ext, str(resize))
                check_and_optimize(tmp_img, ext)
                if CACHE_STORAGE_URL and meta_tag and meta_val:
                    print(os.path.basename(fullpath) + " > Uploading to cache")
                    upload_to_cache(tmp_img, key, meta_tag, meta_val)
            except Exception as exc:
                print(f"{os.path.basename(fullpath)} {exc}")
            finally:
                shutil.move(tmp_img, fullpath)
                print(f"Moved {tmp_img} to {fullpath}")


def interne_link(text_post, domain, nouserprofile, noexternallink):
    body = string2html(text_post)
    links = body.xpath("//a")
    for a in links:
        if "href" in a.attrib:
            root_relative = False
            a_href = re.sub("^https?://", "", a.attrib["href"])
            if a_href == "/":
                if noexternallink:
                    a.attrib.pop("href")
                else:
                    a.attrib["href"] = f"https://{domain}"
                continue
            if len(a_href) >= 2 and a_href[0] == "/" and a_href[1] != "/":
                link = a_href[1:]
                root_relative = True
            elif (
                a_href[0 : len(domain)] == domain
                or a_href[0 : len(domain) + 2] == "//" + domain
            ):
                if a_href[0] == "/":
                    link = a_href[2:]
                else:
                    link = a_href[len(domain) + 1 :]
            else:
                if noexternallink:
                    a.attrib.pop("href")
                continue
            if link[0:2] == "q/" or (
                link[0:10] == "questions/"
                and link[10:17] != "tagged/"
                and link.split("/")[1].isnumeric()
            ):
                is_a = link.split("/")[-1].split("#")
                if len(is_a) == 2 and is_a[0] == is_a[1]:
                    # it a answers
                    qans = is_a[0]
                    a.attrib["href"] = "../element/" + qans + "#a" + qans
                else:
                    # question
                    qid = link.split("/")[1]
                    a.attrib["href"] = "../element/" + qid
            elif link[0:10] == "questions/" and link[10:17] == "tagged/":
                tag = urllib.parse.quote(link.split("/")[-1])
                a.attrib["href"] = "../tag/" + tag + "/1"
            elif link[0:2] == "a/":
                qans_split = link.split("/")
                qans = qans_split[1]
                a.attrib["href"] = "../element/" + qans + "#a" + qans
            elif link[0:6] == "users/":
                userid = link.split("/")[1]
                if not nouserprofile and userid.isnumeric():
                    a.attrib["href"] = "../user/" + userid
                else:
                    if noexternallink:
                        a.attrib.pop("href")
                    else:
                        a.attrib["href"] = f"http://{domain}/{link}"
            elif root_relative:
                if noexternallink:
                    a.attrib.pop("href")
                else:
                    a.attrib["href"] = f"http://{domain}/{link}"

    if links:
        text_post = html2string(body, method="html", encoding="unicode")
    return text_post


def image(text_post, nopic):
    images = os.path.join(output_dir, "static", "images")
    body = string2html(text_post)
    imgs = body.xpath("//img")
    for img in imgs:
        if nopic:
            img.attrib["src"] = ""
        else:
            src = img.attrib["src"]
            ext = os.path.splitext(src.split("?")[0])[1]
            filename = sha256(src.encode("utf-8")).hexdigest() + ext
            out = os.path.join(images, filename)
            # download the image only if it's not already downloaded and if it's not a html
            if not os.path.exists(out) and ext != ".html":
                try:
                    download_image(src, out, resize=540)
                except Exception as e:
                    # do nothing
                    img.attrib["src"] = "../static/images/../../favicon.png"
                    print(e)
                else:
                    src = "../static/images/" + filename
                    img.attrib["src"] = src
                    img.attrib["style"] = "max-width:100%"
            else:
                src = "../static/images/" + filename
                img.attrib["src"] = src
                img.attrib["style"] = "max-width:100%"
    # does the post contain images? if so, we surely modified
    # its content so save it.
    if imgs:
        text_post = html2string(body, method="html", encoding="unicode")
    return text_post


def grab_title_description_favicon_lang(url, do_old):
    if (
        "moderators.meta.stackexchange.com" in url
    ):  # We do this special handling because redirect do not exist; website have change name, but not dump name see issue #80
        get_data = urlopen("https://communitybuilding.meta.stackexchange.com")
    else:
        get_data = urlopen(url)
    if "area51" in get_data.geturl():
        if do_old:
            close_site = {
                "http://arabic.stackexchange.com": "https://web.archive.org/web/20150812150251/http://arabic.stackexchange.com/"
            }
            if url in close_site:
                get_data = urlopen(close_site[url])
            else:
                sys.exit(
                    "This Stack Exchange site has been closed and is not supported by sotoki, please open a issue"
                )
        else:
            print(
                "This Stack Exchange site has been closed and --ignoreoldsite has been pass as argument so we stop"
            )
            sys.exit(0)

    output = get_data.read().decode("utf-8")
    soup = BeautifulSoup.BeautifulSoup(output, "html.parser")
    title = soup.find("meta", attrs={"name": "twitter:title"})["content"]
    description = soup.find("meta", attrs={"name": "twitter:description"})["content"]
    jss = soup.find_all("script")
    lang = "en"
    for js in jss:
        search = re.search(r'StackExchange.init\({"locale":"[^"]*', output)
        if search is not None:
            lang = re.sub(r'StackExchange.init\({"locale":"', "", search.group(0))
    favicon = soup.find("link", attrs={"rel": "icon"})["href"]
    if favicon[:2] == "//":
        favicon = "http:" + favicon
    favicon_out = os.path.join(output_dir, "favicon.png")
    try:
        download_image(
            favicon,
            favicon_out,
            convert_png=True,
            resize=48,
        )
    except Exception as e:
        print(e)
    return [title, description, lang]


def exec_cmd(cmd, timeout=None, workdir=None):
    try:
        ret = None
        ret = subprocess.run(shlex.split(cmd), timeout=timeout, cwd=workdir).returncode
        return ret
    except subprocess.TimeoutExpired:
        print("Timeout ({}s) expired while running: {}".format(timeout, cmd))
    except Exception as e:
        print(e)


def bin_is_present(binary):
    try:
        subprocess.Popen(
            binary,
            universal_newlines=True,
            shell=False,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except OSError:
        return False
    else:
        return True


def dict_to_unicodedict(dictionnary):
    dict_ = {}
    if "OwnerDisplayName" in dictionnary:
        dictionnary["OwnerDisplayName"] = ""
    for k, v in list(dictionnary.items()):
        #        if isinstance(k, str):
        #            unicode_key = k.decode('utf8')
        #        else:
        unicode_key = k
        #        if isinstance(v, str) or type(v) == type({}) or type(v) == type(1):
        unicode_value = v
        #        else:
        #            unicode_value =  v.decode('utf8')
        dict_[unicode_key] = unicode_value

    return dict_


def prepare(dump_path, bin_dir):
    cmd = "bash " + bin_dir + "prepare_xml.sh " + dump_path + " " + bin_dir
    if exec_cmd(cmd) == 0:
        print("Prepare xml ok")
    else:
        sys.exit("Unable to prepare xml :(")


def check_and_optimize(path, ftype):
    if not (path.endswith(f".{ftype}") or (path.endswith(".jpg") and ftype == "jpeg")):
        print(
            f"{os.path.basename(path)} > Extension doesn't match detected file type. Creating temp copy with proper extension to optimize"
        )
        tmp_path = create_temporary_copy(path, suffix=f".{ftype}")
        optimize_one(tmp_path, ftype)
        print(
            f"{os.path.basename(path)} > Copying data from temporary copy to original filepath"
        )
        shutil.copy2(tmp_path, path)
        os.unlink(tmp_path)
    else:
        optimize_one(path, ftype)


def optimize_one(path, ftype):
    if ftype == "jpeg":
        ret = exec_cmd("jpegoptim --strip-all -m50 " + path, timeout=20)
        if ret != 0:
            raise Exception("> jpegoptim failed for " + str(path))
    elif ftype == "png":
        ret = exec_cmd(
            "pngquant --verbose --nofs --force --ext=.png " + path, timeout=20
        )
        if ret != 0:
            raise Exception("> pngquant failed for " + str(path))
        ret = exec_cmd("advdef -q -z -4 -i 5  " + path, timeout=60)
        if ret != 0:
            raise Exception("> advdef failed for " + str(path))
    elif ftype == "gif":
        ret = exec_cmd("gifsicle --batch -O3 -i " + path, timeout=20)
        if ret != 0:
            raise Exception("> gifscale failed for " + str(path))


def resize_one(path, ftype, nb_pix):
    if ftype == "gif":
        ret = exec_cmd("mogrify -resize " + nb_pix + r"x\> " + path, timeout=20)
        if ret != 0:
            raise Exception("> mogrify -resize failed for GIF " + str(path))
    elif ftype in ["png", "jpeg"]:
        try:
            im = Image.open(path)
            ratio = float(nb_pix) / float(im.size[0])
            hsize = int(float(im.size[1]) * ratio)
            im.resize((int(nb_pix), hsize)).save(path, ftype)
        except (KeyError, IOError) as e:
            raise Exception("> Pillow failed to resize\n" + e)


def create_temporary_copy(path, suffix=None):
    path = pathlib.Path(path)
    temp_path = tempfile.NamedTemporaryFile(dir=path.parent, suffix=suffix).name
    shutil.copyfile(path, temp_path)
    return temp_path


def convert_to_png(path, ext):
    if ext == "gif":
        path_tmp = create_temporary_copy(path)
        ret = exec_cmd(
            "gif2apng " + os.path.basename(path_tmp) + " " + os.path.basename(path),
            workdir=os.path.dirname(os.path.abspath(path)),
        )
        os.remove(path_tmp)
        if ret != 0:
            raise Exception("> gif2apng failed for " + str(path))
    else:
        try:
            im = Image.open(path)
            im.save(path, "PNG")
        except (KeyError, IOError) as e:
            raise Exception("> Pillow failed to convert to PNG\n" + e)


def get_hash(site_name):
    digest = None
    sha1hash_url = "https://archive.org/download/stackexchange/stackexchange_files.xml"
    output = urlopen(sha1hash_url).read()
    tree = etree.fromstring(output)
    for file in tree.xpath("/files/file"):
        if file.get("name") == site_name + ".7z":
            print("found")
            digest = file.xpath("sha1")[0].text
    if digest is None:
        print("File :" + site_name + ".7z no found")
        sys.exit(1)
    return digest


def download_dump(domain, dump_path):
    url_dump = "https://archive.org/download/stackexchange/" + domain + ".7z"
    digest = get_hash(domain)
    f = open(domain + ".hash", "w")
    f.write(digest + " " + domain + ".7z")
    f.close()
    exec_cmd("wget " + url_dump)
    if exec_cmd("sha1sum -c " + domain + ".hash") == 0:
        print("Ok we have get dump")
    else:
        print("KO, error will downloading the dump")
        os.remove(domain + ".hash")
        os.remove(domain + ".7z")
        sys.exit(1)
    print(
        "Starting to decompress dump, may take a very long time depending on dump size"
    )
    exec_cmd("7z e " + domain + ".7z -o" + dump_path)
    os.remove(domain + ".hash")
    os.remove(domain + ".7z")


def languageToAlpha3(lang):
    tab = {"en": "eng", "ru": "rus", "pt-BR": "por", "ja": "jpn", "es": "spa"}
    return tab[lang]


def clean(db):
    for elem in ["question", "tag", "user"]:
        elem_path = os.path.join(output_dir, elem)
        if os.path.exists(elem_path):
            print("remove " + elem_path)
            shutil.rmtree(elem_path)
    if os.path.exists(os.path.join(output_dir, "favicon.png")):
        os.remove(os.path.join(output_dir, "favicon.png"))
    if os.path.exists(os.path.join(output_dir, "index")):
        os.remove(os.path.join(output_dir, "index"))
    if os.path.exists(db):
        print("remove " + db)
        os.remove(db)
    if os.path.exists(redirect_file):
        print("remove " + redirect_file)
        os.remove(redirect_file)


def data_from_previous_run(db):
    for elem in ["question", "tag", "user"]:
        elem_path = os.path.join(output_dir, elem)
        if os.path.exists(elem_path):
            return True
    if (
        os.path.exists(os.path.join(output_dir, "favicon.png"))
        or os.path.exists(os.path.join(output_dir, "index"))
        or os.path.exists(db)
        or os.path.exists(redirect_file)
    ):
        return True
    return False


def use_mathjax(domain):
    """const True

    used to be a static list of domains for which mathjax should be enabled.
    this list was updated with help from find_mathml_site.sh script (looks for
    mathjax string in homepage of the domain)"""
    return True


def cache_credentials_ok(cache_storage_url):
    cache_storage = KiwixStorage(cache_storage_url)
    if not cache_storage.check_credentials(
        list_buckets=True, bucket=True, write=True, read=True, failsafe=True
    ):
        print("S3 cache connection error while testing permissions.")
        print(f"  Server: {cache_storage.url.netloc}")
        print(f"  Bucket: {cache_storage.bucket_name}")
        print(f"  Key ID: {cache_storage.params.get('keyid')}")
        print(f"  Public IP: {get_public_ip()}")
        return False
    print(
        "Using optimization cache: "
        + cache_storage.url.netloc
        + " with bucket: "
        + cache_storage.bucket_name
    )
    return True


#########################
#     Zim generation    #
#########################


def create_zims(
    title,
    publisher,
    description,
    domain,
    lang_input,
    zim_path,
    noindex,
    nopic,
    scraper_version,
):
    print("Creating ZIM files")
    if zim_path is None:
        zim_path = dict(
            title=domain.lower(),
            lang=lang_input.lower(),
            date=datetime.datetime.now().strftime("%Y-%m"),
        )
        if nopic:
            zim_path = os.path.join(
                "work/", "{title}_{lang}_all_{date}_nopic.zim".format(**zim_path)
            )
        else:
            zim_path = os.path.join(
                "work/", "{title}_{lang}_all_{date}.zim".format(**zim_path)
            )

    if nopic:
        name = "kiwix." + domain.lower() + ".nopic"
    else:
        name = "kiwix." + domain.lower()
    creator = title
    return create_zim(
        zim_path,
        title,
        description,
        languageToAlpha3(lang_input),
        publisher,
        creator,
        noindex,
        name,
        nopic,
        scraper_version,
        domain,
    )


def create_zim(
    zim_path,
    title,
    description,
    lang_input,
    publisher,
    creator,
    noindex,
    name,
    nopic,
    scraper_version,
    domain,
):
    print("\tWriting ZIM for {}".format(title))

    if nopic:
        tmpfile = tempfile.mkdtemp()
        shutil.move(
            os.path.join(output_dir, "static", "images"),
            os.path.join(tmpfile, "images"),
        )
        shutil.move(
            os.path.join(output_dir, "static", "identicon"),
            os.path.join(tmpfile, "identicon"),
        )

    try:
        make_zim_file(
            build_dir=pathlib.Path(output_dir),
            fpath=pathlib.Path(zim_path),
            name=name,
            main_page="index",
            favicon="favicon.png",
            title=title,
            description=description,
            language=lang_input,
            creator=creator,
            publisher=publisher,
            tags=["_category:stack_exchange", "stackexchange"]
            + (["nopic"] if nopic else []),
            scraper=scraper_version,
            source=f"https://{domain}",
            redirects_file=pathlib.Path(redirect_file),
            without_fulltext_index=True if noindex else False,
            flavour="nopic" if nopic else None,
        )
    except Exception as exc:
        print("Unable to create ZIM file :(")
        print(exc)
        if nopic:
            shutil.move(
                os.path.join(tmpfile, "images"),
                os.path.join(output_dir, "static", "images"),
            )
            shutil.move(
                os.path.join(tmpfile, "identicon"),
                os.path.join(output_dir, "static", "identicon"),
            )
            shutil.rmtree(tmpfile)
        return False
    else:
        print("Successfuly created ZIM file at {}".format(zim_path))
        if nopic:
            shutil.move(
                os.path.join(tmpfile, "images"),
                os.path.join(output_dir, "static", "images"),
            )
            shutil.move(
                os.path.join(tmpfile, "identicon"),
                os.path.join(output_dir, "static", "identicon"),
            )
            shutil.rmtree(tmpfile)
        return True


def run():
    scraper_version = SCRAPER
    try:
        arguments = docopt(__doc__, version=scraper_version)
    except DocoptExit:
        print(__doc__)
        sys.exit()

    print(
        "starting sotoki scraper...{}".format(f"using {TMPFS_DIR}" if TMPFS_DIR else "")
    )
    if arguments["--optimization-cache"] is not None:
        if not cache_credentials_ok(arguments["--optimization-cache"]):
            raise ValueError(
                "Bad authentication credentials supplied for optimization cache. Please try again."
            )
        global CACHE_STORAGE_URL
        CACHE_STORAGE_URL = arguments["--optimization-cache"]
    else:
        print("No cache credentials provided. Continuing without optimization cache")

    # Check binary
    for binary in [
        "bash",
        "jpegoptim",
        "pngquant",
        "advdef",
        "gifsicle",
        "mogrify",
        "gif2apng",
        "wget",
        "sha1sum",
        "7z",
        "sed",
        "sort",
        "rm",
        "grep",
    ]:
        if not bin_is_present(binary):
            sys.exit(binary + " is not available, please install it.")
    tag_depth = int(arguments["--tag-depth"])
    if tag_depth != -1 and tag_depth <= 0:
        sys.exit("--tag-depth should be a positive integer")
    domain = arguments["<domain>"]
    url = domain
    if re.match("^https?://", url):
        domain = re.sub("^https?://", "", domain).split("/")[0] 
    url = "https://" + domain
    publisher = arguments["<publisher>"]

    if not os.path.exists("work"):
        os.makedirs("work")

    if arguments["--directory"] == "download":
        dump = os.path.join("work", re.sub(r"\.", "_", domain))
    else:
        dump = arguments["--directory"]

    global output_dir
    output_dir = os.path.join(dump, "output")
    db = os.path.join(dump, "se-dump.db")
    global redirect_file
    redirect_file = os.path.join(dump, "redirection.csv")

    # set ImageMagick's temp folder via env
    magick_tmp = os.path.join(dump, "magick")
    if os.path.exists(magick_tmp):
        shutil.rmtree(magick_tmp)
    os.makedirs(magick_tmp)
    os.environ.update({"MAGICK_TEMPORARY_PATH": magick_tmp})

    if arguments["--threads"] is not None:
        cores = int(arguments["--threads"])
    else:
        cores = cpu_count() / 2 or 1

    if arguments["--reset"]:
        if os.path.exists(dump):
            for elem in [
                "Badges.xml",
                "Comments.xml",
                "PostHistory.xml",
                "Posts.xml",
                "Tags.xml",
                "usersbadges.xml",
                "Votes.xml",
                "PostLinks.xml",
                "prepare.xml",
                "Users.xml",
            ]:
                elem_path = os.path.join(dump, elem)
                if os.path.exists(elem_path):
                    os.remove(elem_path)
        arguments["--directory"] = "download"

    if arguments["--reset-images"]:
        if os.path.exists(os.path.join(dump, "output")):
            shutil.rmtree(os.path.join(dump, "output"))

    if arguments["--clean-previous"]:
        clean(db)

    if data_from_previous_run(db):
        sys.exit(
            "There is still data from a previous run, you can trash them by adding --clean-previous as argument"
        )

    if not os.path.exists(dump):
        os.makedirs(dump)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    if not os.path.exists(os.path.join(output_dir, "common_images")):
        os.makedirs(os.path.join(output_dir, "common_images"))
    if not os.path.exists(os.path.join(output_dir, "static", "images")):
        os.makedirs(os.path.join(output_dir, "static", "images"))

    title, description, lang_input = grab_title_description_favicon_lang(
        url, not arguments["--ignoreoldsite"]
    )

    if not os.path.exists(
        os.path.join(dump, "Posts.xml")
    ):  # If dump is not here, download it
        if domain == "stackoverflow.com":
            for part in [
                "stackoverflow.com-Badges",
                "stackoverflow.com-Comments",
                "stackoverflow.com-PostLinks",
                "stackoverflow.com-Posts",
                "stackoverflow.com-Tags",
                "stackoverflow.com-Users",
            ]:
                dump_tmp = os.path.join("work", re.sub(r"\.", "_", part))
                os.makedirs(dump_tmp)
                download_dump(part, dump_tmp)
            for path in [
                os.path.join("work", "stackoverflow_com-Badges", "Badges.xml"),
                os.path.join("work", "stackoverflow_com-Comments", "Comments.xml"),
                os.path.join("work", "stackoverflow_com-PostLinks", "PostLinks.xml"),
                os.path.join("work", "stackoverflow_com-Posts", "Posts.xml"),
                os.path.join("work", "stackoverflow_com-Tags", "Tags.xml"),
                os.path.join("work", "stackoverflow_com-Users", "Users.xml"),
            ]:
                filename = os.path.basename(path)
                os.rename(path, os.path.join(dump, filename))
                shutil.rmtree(os.path.dirname(path))
        else:
            download_dump(domain, dump)

    templates = os.path.join(os.path.abspath(os.path.dirname(__file__)), "templates")

    # prepare db
    conn = sqlite3.connect(db)  # can be :memory: for small dump
    conn.row_factory = dict_factory
    cursor = conn.cursor()
    # create table tags-questions
    sql = "CREATE TABLE IF NOT EXISTS questiontag(id INTEGER PRIMARY KEY AUTOINCREMENT UNIQUE, Score INTEGER, Title TEXT, QId INTEGER, CreationDate TEXT, Tag TEXT)"
    cursor.execute(sql)
    # creater user table
    sql = "CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY UNIQUE, DisplayName TEXT, Reputation TEXT)"
    cursor.execute(sql)
    # create table for links
    sql = "CREATE TABLE IF NOT EXISTS links(id INTEGER, title TEXT)"
    cursor.execute(sql)
    conn.commit()

    jinja_init(templates)
    global MARKDOWN
    renderer = mistune.HTMLRenderer()
    MARKDOWN = mistune.Markdown(renderer, plugins=[plugin_url])
    if not os.path.exists(
        os.path.join(dump, "prepare.xml")
    ):  # If we haven't already prepare
        prepare(dump, os.path.abspath(os.path.dirname(__file__)) + "/")

    # Generate users !
    parser = make_parser()
    parser.setContentHandler(
        UsersRender(
            templates,
            title,
            publisher,
            dump,
            cores,
            cursor,
            conn,
            url,
            use_mathjax(domain),
            arguments["--nopic"],
            arguments["--no-identicons"],
            arguments["--no-userprofile"],
            arguments["--no-externallink"],
            domain,
        )
    )
    parser.parse(os.path.join(dump, "usersbadges.xml"))
    conn.commit()

    # Generate question !
    parser = make_parser()
    parser.setContentHandler(
        QuestionRender(
            templates,
            title,
            publisher,
            dump,
            cores,
            cursor,
            conn,
            url,
            domain,
            use_mathjax(domain),
            arguments["--nopic"],
            arguments["--no-userprofile"],
            arguments["--no-externallink"],
            arguments["--no-unansweredquestion"],
        )
    )
    parser.parse(os.path.join(dump, "prepare.xml"))
    conn.commit()

    # Generate tags !
    parser = make_parser()
    parser.setContentHandler(
        TagsRender(
            templates,
            title,
            publisher,
            dump,
            cores,
            cursor,
            conn,
            tag_depth,
            description,
            use_mathjax(domain),
        )
    )
    parser.parse(os.path.join(dump, "Tags.xml"))
    conn.close()

    # remove magick tmp folder (not reusable)
    shutil.rmtree(magick_tmp, ignore_errors=True)

    # copy static
    if use_mathjax(domain):
        copy_tree(
            os.path.join(os.path.abspath(os.path.dirname(__file__)), "static_mathjax"),
            os.path.join(output_dir, "static"),
        )
    copy_tree(
        os.path.join(os.path.abspath(os.path.dirname(__file__)), "static"),
        os.path.join(output_dir, "static"),
    )
    if not arguments["--nozim"]:
        done = create_zims(
            title,
            publisher,
            description,
            domain,
            lang_input,
            arguments["--zimpath"],
            arguments["--nofulltextindex"],
            arguments["--nopic"],
            scraper_version,
        )
        if done:
            clean(db)
        if not done:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
