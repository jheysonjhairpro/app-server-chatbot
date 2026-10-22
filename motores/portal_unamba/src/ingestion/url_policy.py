"""Alcance autorizado: portal principal de UNAMBA, sin subdominios."""

import re
from urllib.parse import urlsplit, unquote, urldefrag

HOSTS = {'www.unamba.edu.pe', 'unamba.edu.pe'}
POLICY = 'unamba_main_visible_v1'


def allowed_url(url):
    try:
        p = urlsplit(url)
        return (p.scheme in {'http', 'https'} and p.hostname in HOSTS
                and not p.username and not p.password and p.port in {None, 80, 443}
                and not re.search(r'[\x00-\x20\x7f]', url))
    except (ValueError, TypeError):
        return False


def html_candidate(url):
    if not allowed_url(url):
        return False
    p = urlsplit(url)
    path = unquote(p.path).lower()
    if re.search(r'/(?:wp-admin|wp-json|wp-login\.php|xmlrpc\.php|feed|rss)(?:/|$)', path):
        return False
    if re.search(r'\.(?:pdf|xml|json|rss|zip|rar|7z|docx?|xlsx?|pptx?|jpg|jpeg|png|gif|webp|svg|ico|mp[34]|css|js|woff2?|ttf)(?:/)?$', path):
        return False
    return not re.search(r'(?:^|&)(?:rest_route|preview|feed|s|author|attachment_id)=', p.query, re.I)


def page_url(url):
    return urldefrag(url)[0]
