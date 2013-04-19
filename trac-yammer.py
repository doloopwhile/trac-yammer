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
from pyquery import PyQuery
import yaml

from datetime import date, datetime, timedelta

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
  goo_gl_api_url
  logfile_path
""".split()))

class Config:
  @classmethod
  def load(cls, config_file_path, *a, **kw):
    with io.open(config_file_path, 'rb') as fp:
      kw.update(yaml.load(fp))
    return cls(*a, **kw)

  def __init__(self, date, **kw):
    self._date = date
    for key_name in _Config_key_names:
      value = kw[key_name]
      setattr(self, '_' + key_name, value)

  def date(self):
    return self._date


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
  params['from'] = config.date().strftime('%Y/%m/%d').encode('utf-8')
  return config.feed_url_base() + '?' + urllib.urlencode(params)


def create_message_body(config):
  feed = feedparser.parse(get_feed_url(config))
  if not feed.entries:
    return '{}日はWikiの更新はありませんでした。'.format(config.date().day)

  fp = io.StringIO()
  fp.write('{}日のWikiの更新は以下の通りです:\n\n'.format(config.date().day))

  pages = {}
  for entry in feed.entries:
    p = urlparse.urlparse(entry.link)

    # トップページからのパス
    path = posixpath.relpath(p.path, config.navona_wiki_path())

    if path not in pages:
      # バージョン等を除いたURL
      link = p.scheme + '://' + p.netloc + p.path
      pages[path] = (link, [])

    if entry.description:
      desc = PyQuery(entry.description).find('p').text()
      if desc:
        pages[path][1].append(desc)

  for path, (link, descriptions) in pages.items():
    link = goo_gl_shorten(link, config.goo_gl_api_url())
    fp.write(u'{path}<{link}>\n'.format(path=path, link=link))
    for desc in descriptions:
      if desc:
        fp.write(u'... {desc}\n'.format(desc=desc))

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


def main():
  parser = ArgumentParser()
  parser.add_argument('--config-file', action='store')
  parser.add_argument('--date', action='store', default=None)
  parser.add_argument('--fetch-token', action='store_true')

  args = parser.parse_args()

  if args.date is not None:
    d = dateutil.parser.parse(args.date)
  else:
    d = date.today() - timedelta(1)

  config = Config.load(
    date = d,
    config_file_path = args.config_file
  )

  logging.basicConfig(
    filename=config.logfile_path(),
    format='%(asctime)s %(levelname)s %(message)s'
  )

  if args.fetch_token:
    fetch_token_to_file(config)
    sys.exit()

  client = create_client(config)

  params = dict(
    group_id=config.group_id(),
    body=create_message_body(config).encode(u'utf-8'),
    broadcast=True
  )

  resp, content = client.request(config.messages_url(), 'POST', urllib.urlencode(params))
  logging.info("response-status={}".format(resp['status']))
  if not resp['status'].startswith('2'):
    logging.info("response-content={}".format(resp['status']))
    print(content)
    sys.exit(1)


if __name__ == '__main__':
  main()

