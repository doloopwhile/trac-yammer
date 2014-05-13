#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals

from argparse import ArgumentParser
from datetime import date, datetime, timedelta
from email.utils import formatdate
from itertools import groupby
import csv
import io
import json
import logging
import posixpath
import sys
import time
import urllib
import urllib2
import urlparse

from pyquery import PyQuery
import feedparser
import yaml
import yampy


_Config_key_names = list(map(str, """
    group_id
    client_id
    client_secret
    access_token
    messages_url
    auth_url
    goo_gl_api_url
    logfile_path
    history_file_path
""".split()))


class Config:
    @classmethod
    def load(cls, config_file_path, *a, **kw):
        with io.open(config_file_path, 'rb') as fp:
            kw.update(yaml.load(fp))
        return cls(*a, **kw)

    def __init__(self, **kw):
        for key_name in _Config_key_names + ['wikis']:
            value = kw[key_name]
            setattr(self, '_' + key_name, value)

    def date(self):
        return self._date

    def begin_date(self):
        return self._begin_date

    def last_date(self):
        return self._last_date

    def set_date_range(self, begin_date, last_date):
        self._begin_date = begin_date
        self._last_date = last_date

    def wikis(self):
        return [WikiConfig(**wiki_dict) for wiki_dict in self._wikis]

    def __getattr__(self, name):
        attr_name = '_' + name
        if hasattr(self, attr_name):
            def get():
                return getattr(self, attr_name)
            get.__name__ = name
            return get
        raise AttributeError(name)


class WikiConfig:
    def __init__(self, name, netloc, base_path, feed_url_base):
        self._name = name
        self._netloc = netloc
        self._base_path = base_path
        self._feed_url_base = feed_url_base

    def __getattr__(self, name):
        attr_name = '_' + name
        if hasattr(self, attr_name):
            def get():
                return getattr(self, attr_name)
            get.__name__ = name
            return get
        raise AttributeError(name)


def goo_gl_shorten(longUrl, api_url):
    data = json.dumps(dict(longUrl=longUrl))
    req = urllib2.Request(api_url, data)
    req.add_header('Content-Type', 'application/json')

    fp = urllib2.urlopen(req)
    return json.loads(fp.read())['id']


def get_feed_url(config, wiki):
    params = dict(
        daysback=0,
        wiki='on',
        format='rss',
    )
    params['from'] = config.last_date().strftime('%Y/%m/%d').encode('utf-8')
    params['daysback'] = (config.last_date() - config.begin_date()).days
    return wiki.feed_url_base() + '?' + urllib.urlencode(params)


def create_message_body(config):
    fp = io.StringIO()
    for wiki in config.wikis():
        feed = feedparser.parse(get_feed_url(config, wiki))

        if config.begin_date() < config.last_date():
            days = "{begin_day}日から{last_day}日".format(
                begin_day=config.begin_date().day,
                last_day=config.last_date().day
            )
        else:
            days = "{day}日".format(day=config.last_date().day)

        if not feed.entries:
            fp.write('{days}は{wiki_name}の更新はありませんでした。\n'.format(
                days=days, wiki_name=wiki.name()))
            continue

        fp.write('{days}の{wiki_name}の更新は以下の通りです:\n'.format(
            days=days, wiki_name=wiki.name()))

        pages = {}

        def entry_path(entry):
            return urlparse.urlparse(entry.link).path

        def entry_version(entry):
            p = urlparse.urlparse(entry.link)
            v = urlparse.parse_qs(p.query).get('version')
            try:
                return int(v[0])
            except (TypeError, IndexError, ValueError):
                return 0

        # 同じパスの変更が同じグループになるようにソートする
        # Python のソートは安定なので同じパスの変更どうしは日付順に並ぶ
        entries = list(feed.entries)
        entries.sort(key=entry_path)

        for page_path, page_entries in groupby(entries, entry_path):
            page_entries = list(page_entries)

            # diffへのURL
            old_version = min(map(entry_version, page_entries))

            link = urlparse.urlunparse((
                'http',
                wiki.netloc(),
                page_path,
                '',
                urllib.urlencode(dict(action='diff', old_version=old_version)),
                '',
            ))

            short_link = goo_gl_shorten(link, config.goo_gl_api_url())
            page_rel_path = posixpath.relpath(
                page_path,
                wiki.base_path()
            )
            fp.write(u'{page_rel_path}<{short_link}>\n'.format(
                page_rel_path=page_rel_path,
                short_link=short_link,
            ))

            for entry in sorted(page_entries, key=entry_version):
                if not entry.description:
                    continue
                description = PyQuery(entry.description).find('p').text()
                if not description:
                    continue
                fp.write(u'... {description}\n'.format(description=description))
        fp.write('\n')

    return fp.getvalue()


def append_history(config):
    with io.open(config.history_file_path(), 'ab') as fp:
        writer = csv.writer(fp)
        writer.writerow([
            formatdate(time.time()).encode('ascii'),
            config.begin_date().strftime('%Y-%m-%d').encode('ascii'),
            config.last_date().strftime('%Y-%m-%d').encode('ascii'),
        ])


def load_date_range(config, begin_date=None, last_date=None):
    if last_date is None:
        last_date = date.today() - timedelta(days=1)

    if begin_date is None:
        try:
            row = ()
            with io.open(config.history_file_path(), 'rb') as fp:
                for row in csv.reader(fp):
                    pass
            s = row[2]
            prev_last_date = datetime.strptime(s, "%Y-%m-%d").date()
            begin_date = prev_last_date + timedelta(days=1)
        except (ValueError, IndexError, OSError, IOError,
                UnicodeDecodeError) as e:
            logging.warning(e)
            begin_date = last_date

    return (begin_date, last_date)


def parse_date_if(date_string):
    if date_string is None:
        return None
    return datetime.strptime(date_string, '%Y-%m-%d').date()


def main():
    parser = ArgumentParser()
    parser.add_argument('--config-file', action='store')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--begin-date', action='store', default=None)
    parser.add_argument('--last-date', action='store', default=None)

    args = parser.parse_args()

    config = Config.load(config_file_path=args.config_file)
    logging.basicConfig(
        filename=config.logfile_path(),
        format='%(asctime)s %(levelname)s %(message)s'
    )

    begin_date, last_date = load_date_range(
        config,
        parse_date_if(args.begin_date),
        parse_date_if(args.last_date),
    )
    config.set_date_range(begin_date, last_date)

    if begin_date > last_date:
        logging.info("begin_date {} is later than end_date {}, do nothing")
        sys.exit(1)
    elif not args.dry_run:
        yammer = yampy.Yammer(access_token=config.access_token())

        yammer.messages.create(
            create_message_body(config).encode(u'utf-8'),
            group_id=config.group_id(),
        )

    append_history(config)


if __name__ == '__main__':
    main()
