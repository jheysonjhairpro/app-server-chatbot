"""Rastrea solo enlaces accesibles en la interfaz renderizada del portal UNAMBA.
Python 3.10 o posterior; requiere Playwright y Chromium.
"""
import argparse
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit, urldefrag, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

PORTADA = 'https://www.unamba.edu.pe/'
HOST = 'www.unamba.edu.pe'
from configuracion.entorno import ruta
from motores.portal_unamba.src.ingestion.url_policy import allowed_url, html_candidate, page_url, POLICY

def permitido(url):
    return html_candidate(url)

def interno(url):
    """Solo este host puede aportar nuevos enlaces al recorrido."""
    return permitido(url)

class RedireccionSegura(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not permitido(newurl):
            raise ValueError('Redirección a URL no permitida: ' + newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def descargar(url, timeout):
    if not permitido(url):
        raise ValueError('URL no permitida')
    # Codifica caracteres Unicode sin modificar escapes o separadores existentes.
    url = quote(url, safe=':/?&=#%+;,@!$\'()*[]~-._')
    req = Request(url, headers={'User-Agent': 'UNAMBA-LinkChecker/1.0',
                               'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8'})
    return build_opener(RedireccionSegura()).open(req, timeout=timeout)

class Pagina(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.enlaces = []
        self.actual = None
        self.ids = set()
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get('id'):
            self.ids.add(a['id'])
        if tag == 'a':
            if a.get('name'):
                self.ids.add(a['name'])
            if a.get('href'):
                self.actual = {'href': a['href'], 'partes': [],
                               'titulo': a.get('aria-label') or a.get('title', '')}
                self.enlaces.append(self.actual)
        if tag == 'img' and self.actual and a.get('alt'):
            self.actual['partes'].append(a['alt'])
    def handle_data(self, data):
        if self.actual is not None:
            self.actual['partes'].append(data)
    def handle_endtag(self, tag):
        if tag == 'a':
            self.actual = None

def analizar(body, charset):
    p = Pagina()
    p.feed(body.decode(charset or 'utf-8', errors='replace'))
    return p


# Solo enlaces que el usuario puede ver, también dentro de menús desplegados.
VISIBLE_LINKS = r"""() => Array.from(document.querySelectorAll('a[href]')).filter(a => {
    if (!a.getClientRects().length || a.closest('[hidden],[inert],[aria-hidden="true"]') || a.matches('[aria-disabled="true"]')) return false;
    if (a.checkVisibility && !a.checkVisibility({checkOpacity:true, checkVisibilityCSS:true})) return false;
    const rect=a.getBoundingClientRect();
    if (!rect.width || !rect.height || getComputedStyle(a).pointerEvents==='none') return false;
    for(let p=a;p;p=p.parentElement){const s=getComputedStyle(p),r=p.getBoundingClientRect();
        if(s.display==='none'||s.visibility==='hidden'||Number(s.opacity)===0)return false;
        if(/hidden|clip/.test(s.overflow+s.overflowX+s.overflowY) && (rect.right<=r.left||rect.left>=r.right||rect.bottom<=r.top||rect.top>=r.bottom))return false;
    }
    return !!(a.innerText.trim() || a.getAttribute('aria-label') || a.querySelector('img[alt]')?.alt || a.getAttribute('title'));
}).map(a=>({href:a.href, partes:[a.innerText.trim() || a.querySelector('img[alt]')?.alt || ''], titulo:a.getAttribute('aria-label')||a.title||''}))"""


class BrowserDiscovery:
    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self.runtime = sync_playwright().start()
        try:
            self.browser = self.runtime.chromium.launch(headless=True)
            self.context = self.browser.new_context(viewport={'width': 1440, 'height': 1000}, service_workers='block')
            self.context.route('**/*', self.guard)
            self.page = self.context.new_page()
        except Exception:
            self.runtime.stop()
            raise RuntimeError('No se pudo abrir Chromium. Ejecuta preparar_entorno.bat para instalar el navegador del rastreador.')
        return self

    @staticmethod
    def guard(route):
        request = route.request
        # Evita visitar páginas y redirecciones externas, incluso en iframes.
        if request.is_navigation_request() and not html_candidate(request.url):
            route.abort()
        elif not allowed_url(request.url) and request.resource_type not in {'stylesheet', 'script', 'image', 'font'}:
            route.abort()
        else:
            route.continue_()

    def render(self, url, timeout):
        response = self.page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
        self.page.wait_for_timeout(700)
        final = self.page.url
        if not response or not permitido(final) or not 200 <= response.status < 300:
            raise ValueError('Página fuera de alcance, inaccesible o sin respuesta HTML.')
        tipo = response.headers.get('content-type', '').split(';')[0]
        if tipo not in {'text/html', 'application/xhtml+xml'}:
            raise ValueError('El enlace no es una página HTML.')
        if re.search(r'404|page not found|p[aá]gina no encontrada', self.page.title(), re.I):
            raise ValueError('Posible página de error.')
        links = {}

        def dismiss_notice():
            # Cerrar avisos con su control visible, sin ocultar elementos por CSS.
            closers = self.page.locator('button.pum-close, [role="dialog"] button[aria-label="Cerrar"], [role="dialog"] button[aria-label="Close"]')
            for index in range(min(closers.count(), 5)):
                close = closers.nth(index)
                if close.is_visible():
                    try:
                        close.click(timeout=1000)
                    except Exception:
                        pass

        def capture():
            for link in self.page.evaluate(VISIBLE_LINKS):
                if permitido(link['href']):
                    links.setdefault(link['href'], link)

        dismiss_notice()
        capture()
        # Hover abre submenús CSS; los botones expandibles permiten el menú móvil.
        for selector, action in [('nav a[aria-expanded], header a[aria-haspopup], .menu-item-has-children > a, .mega-menu-item-has-children > a', 'hover'),
                                 ('nav button[aria-expanded="false"], header button[aria-expanded="false"], button[aria-controls][aria-expanded="false"], nav a[aria-haspopup="true"], header a[data-bs-toggle="dropdown"], header a[data-toggle="dropdown"], nav a.dropdown-toggle, nav a[aria-expanded="false"], header a[aria-expanded="false"], .mega-menu-link[aria-expanded="false"]', 'click')]:
            controls = self.page.locator(selector)
            for index in range(min(controls.count(), 120)):
                item = controls.nth(index)
                try:
                    dismiss_notice()
                    if not item.is_visible():
                        continue
                    if action == 'hover':
                        item.hover(timeout=700)
                    elif item.get_attribute('type') != 'submit' and (not item.get_attribute('href') or item.get_attribute('href').startswith('#')):
                        item.click(timeout=700)
                    if self.page.url != final:
                        self.page.goto(final, wait_until='domcontentloaded', timeout=timeout * 1000)
                    self.page.wait_for_timeout(350)
                    capture()
                except Exception:
                    continue
        ids = self.page.locator('[id]').evaluate_all('(items)=>items.map(a=>a.id)')
        return {'ok': True, 'estado_http': response.status, 'url_final': final, 'tipo': tipo,
                'enlaces': list(links.values()), 'ids': set(ids)}

    def __exit__(self, *_):
        self.browser.close()
        self.runtime.stop()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--salida', type=Path, default=ruta("PORTAL_CRAWL_DIR", "datos/portal/crawl"))
    ap.add_argument('--timeout', type=float, default=float(os.getenv("PORTAL_CRAWL_TIMEOUT", "25")))
    ap.add_argument('--trabajadores', type=int, default=1, help='Compatibilidad: el navegador recorre secuencialmente.')
    ap.add_argument('--max-paginas', type=int, default=int(os.getenv("PORTAL_CRAWL_MAX_PAGES", "200")))
    ap.add_argument('--objetivo', action='append', default=[])
    args = ap.parse_args(argv)
    if args.max_paginas < 0:
        ap.error('--max-paginas no puede ser negativo')
    if any(not permitido(url) for url in args.objetivo):
        ap.error('Solo se permiten páginas del portal principal de UNAMBA.')
    args.salida.mkdir(parents=True, exist_ok=True)
    candidates = {PORTADA: {'url': PORTADA, 'origen': PORTADA, 'textos': ['Portada UNAMBA']}}
    states, rejected = {}, []
    targets = {page_url(url) for url in args.objetivo}
    with BrowserDiscovery() as browser:
        while True:
            pending = [url for url in candidates if url not in states]
            if not pending or args.max_paginas and len(states) >= args.max_paginas:
                break
            # Priorizar un objetivo solo después de descubrirlo en la interfaz.
            url = next((url for url in pending if url in targets), pending[0])
            try:
                result = browser.render(url, args.timeout)
                if not permitido(result['url_final']):
                    raise ValueError('Redirección fuera del portal principal.')
                states[url] = result
                for link in result.get('enlaces', []):
                    target = page_url(urljoin(result['url_final'], link['href']))
                    if not permitido(target):
                        continue
                    candidates.setdefault(target, {'url': target, 'origen': result['url_final'],
                        'textos': [' '.join(link.get('partes', [])) or link.get('titulo', '')]})
                if url == PORTADA:
                    (args.salida / 'portada_enlaces.json').write_text(json.dumps([link for link in result.get('enlaces', []) if permitido(urljoin(result['url_final'], link['href']))], ensure_ascii=False, indent=2), encoding='utf-8')
            except Exception as exc:
                states[url] = {'ok': False, 'motivo': str(exc)}
            print(f'Páginas comprobadas en navegador: {len(states)}; enlaces internos visibles: {len(candidates)}', flush=True)
            if targets and all(target in states and states[target].get('ok') for target in targets):
                break
    valid = []
    for url, row in candidates.items():
        result = states.get(url, {'ok': False, 'motivo': 'Pendiente por límite de páginas'})
        entry = {**row, **{key: value for key, value in result.items() if key not in {'ok', 'enlaces', 'ids'}}}
        (valid if result.get('ok') else rejected).append(entry)
    summary = {'portada': PORTADA, 'host_recursivo': HOST, 'policy': POLICY,
               'verificado_utc': datetime.now(timezone.utc).isoformat(),
               'alcance': 'Solo portal principal UNAMBA; enlaces visibles en navegador y menús desplegables; sin portadas externas.',
               'destinos_verificados': len(states), 'rastreo_completo': len(states) == len(candidates),
               'total_validos': len(valid), 'enlaces': valid}
    (args.salida / 'enlaces_validos.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    (args.salida / 'enlaces_validos.txt').write_text('\n'.join(row.get('url_final', row['url']) for row in valid) + '\n', encoding='utf-8')
    (args.salida / 'descartados.json').write_text(json.dumps(rejected, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Páginas HTML accesibles: {len(valid)}. Salida: {args.salida}', flush=True)
    if not valid:
        raise ValueError('No se pudo acceder a la portada de UNAMBA. No se autoriza ninguna extracción.')


if __name__ == '__main__':
    main()
