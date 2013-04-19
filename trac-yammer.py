#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function, unicode_literals
import os
import sys
import io
import time
import urllib
import json
import urllib2
import dateutil.parser
import urlparse
from argparse import ArgumentParser
import posixpath
import logging
import csv
import time
from email.utils import formatdate
from datetime import date, datetime, timedelta
from itertools import groupby


from pyquery import PyQuery
import yaml
import oauth2 as oauth
import feedparser

_Config_key_names = list(map(str, """
    group_id
    token_store_file_path
    consumer_key
    consumer_secret
    request_token_url
    access_token_url
    messages_url
    auth_url
    feed_url_base
    navona_wiki_path
    navona_wiki_netloc
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
        for key_name in _Config_key_names:
            value = kw[key_name]
            setattr(self, '_' + key_name, value)

    def date(self):
        return self._date

    def begin_date(self):
        return self._begin_date

    def set_begin_date(self, begin_date):
        self._begin_date = begin_date

    def last_date(self):
        return self._last_date

    def set_last_date(self, last_date):
        self._last_date = last_date

for key_name in _Config_key_names:
    def create_getter_method(key_name=key_name):
        def getter(self):
            return getattr(self, "_" + key_name)
        getter.__name__ = key_name
        setattr(Config, key_name, getter)
    create_getter_method()


def goo_gl_shorten(longUrl, api_url):
    data = json.dumps(dict(longUrl=longUrl))
    req = urllib2.Request(api_url, data)
    req.add_header('Content-Type', 'application/json')

    fp = urllib2.urlopen(req)
    return json.loads(fp.read())['id']


def prompt_verifier(request_token, url):
    print("Go to the following link in your browser:")
    print("{}?oauth_token={}".format(url, request_token.key))
    return raw_input('What is the PIN? ')


def load_auth_token(path):
    with io.open(path, 'rb') as fp:
        x = json.load(fp)
        return oauth.Token(x['auth_key'], x['auth_secret'])


def save_auth_token(path, auth_token):
    with io.open(path, 'wb') as fp:
        json.dump(dict(
            auth_key=auth_token.key,
            auth_secret=auth_token.secret
        ), fp)


def get_feed_url(config):
    params = dict(
        daysback=0,
        wiki='on',
        format='rss',
    )
    params['from'] = config.last_date().strftime('%Y/%m/%d').encode('utf-8')
    params['daysback'] = (config.last_date() - config.begin_date()).days
    return config.feed_url_base() + '?' + urllib.urlencode(params)


def create_message_body(config):
    feed = feedparser.parse(get_feed_url(config))

    if config.begin_date() < config.last_date():
        days = "{begin_day}日から{last_day}日".format(
            begin_day=config.begin_date().day,
            last_day=config.last_date().day
        )
    else:
        days = "{day}日".format(day=config.last_date().day)

    if not feed.entries:
        return '{}はWikiの更新はありませんでした。'.format(days)

    fp = io.StringIO()
    fp.write('のWikiの更新は以下の通りです:\n\n'.format(days))

    pages = {}

    def entry_path(entry):
        return urlparse.urlparse(entry.link).path

    def entry_version(entry):
        p = urlparse.urlparse(entry.link)
        return urlparse.parse_qs(p.query).get('version')[0]

    for page_path, page_entries in groupby(feed.entries, entry_path):
        page_entries = list(page_entries)

        # diffへのURL
        old_version = min(map(entry_version, page_entries))

        link = urlparse.urlunparse((
            'http',
            config.navona_wiki_netloc(),
            page_path,
            '',
            urllib.urlencode(dict(action='diff', old_version=old_version)),
            '',
        ))

        short_link = goo_gl_shorten(link, config.goo_gl_api_url())
        page_rel_path = posixpath.relpath(
            page_path,
            config.navona_wiki_path()
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

    return fp.getvalue()


def create_consumer(config):
    return oauth.Consumer(
        key=config.consumer_key(),
        secret=config.consumer_secret()
    )


def create_client(config):
    consumer = create_consumer(config)
    auth_token = load_auth_token(config.token_store_file_path())
    return oauth.Client(consumer, auth_token)


def request_auth_token(config):
    consumer = create_consumer(config)
    client = oauth.Client(consumer)

    resp, content = client.request(config.request_token_url(), "GET")
    if resp['status'] != '200':
            raise Exception("Invalid response %s." % resp['status'])
    x = dict(urlparse.parse_qsl(content))

    token = oauth.Token(x['oauth_token'], x['oauth_token_secret'])
    token.set_verifier(prompt_verifier(token, config.auth_url()))
    client = oauth.Client(consumer, token)

    resp, content = client.request(config.access_token_url(), "POST")
    x = dict(urlparse.parse_qsl(content))

    return oauth.Token(
        key=x['oauth_token'],
        secret=x['oauth_token_secret']
    )


def fetch_token_to_file(config):
    auth_token = request_auth_token(config)
    save_auth_token(config.token_store_file_path(), auth_token)


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
            previ_last_date = datetime.strptime(s, "%Y-%m-%d").date()
            begin_date = prev_last_date + timedelta(days=1)
        except (ValueError, IndexError, OSError, IOError,
                UnicodeDecodeError) as e:
            logging.warning(e)
            begin_date = last_date

    return (begin_date, last_date)


def parse_date_if(date_string):
    if date_string is None:
        return None
    return datetime.strptime(date_string, '%Y-%m-%d')


def main():
    parser = ArgumentParser()
    parser.add_argument('--config-file', action='store')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--begin-date', action='store', default=None)
    parser.add_argument('--last-date', action='store', default=None)
    parser.add_argument('--fetch-token', action='store_true')

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
    config.set_begin_date(begin_date)
    config.set_last_date(last_date)

    if begin_date > last_date:
        logging.info("begin_date {} is later than end_date {}, do nothing")
        sys.exit(1)
    elif not args.dry_run:
        if args.fetch_token:
            fetch_token_to_file(config)
            sys.exit()

        client = create_client(config)

        params = urllib.urlencode(dict(
            group_id=config.group_id(),
            body=create_message_body(config).encode(u'utf-8'),
            broadcast=True
        ))

        resp, content = client.request(config.messages_url(), 'POST', params)
        logging.info("response-status={}".format(resp['status']))
        if not resp['status'].startswith('2'):
            logging.warning("response-content={}".format(resp['status']))
            sys.exit(1)

    append_history(config)


if __name__ == '__main__':
    main()
