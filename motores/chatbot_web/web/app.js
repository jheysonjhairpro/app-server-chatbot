const $ = (id) => document.getElementById(id);
let offset = 0,
  total = 0,
  selected = '',
  listRequest = 0,
  detailRequest = 0;
let filters = {
  q: '',
  categoria: '',
  orden: 'recientes'
};
let tableView = false,
  currentItems = [];
const qualityLabels = {
  sin_fecha: 'Sin fecha',
  sin_titulo: 'Sin título',
  vacio: 'Sin texto',
  breve: 'Texto breve',
  titulo_repetido: 'Título repetido',
  contenido_repetido: 'Contenido repetido'
};
const filterInputs = {
  q: 'query',
  categoria: 'category',
  orden: 'order',
  direccion: 'direction',
  campo: 'field',
  portal: 'portal',
  documentos: 'docs',
  calidad: 'quality',
  fecha: 'date-type',
  desde: 'since',
  hasta: 'until'
};
const limit = 20;
const number = (n) => new Intl.NumberFormat('es-PE').format(n);
const date = (value) => value ? new Date(value).toLocaleDateString('es-PE') : 'Sin fecha';

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function link(label, url, className) {
  const element = node('a', label, className);
  const href = safeUrl(url);
  if (href) {
    element.href = href;
    element.target = '_blank';
    element.rel = 'noopener noreferrer';
  }
  return element;
}
async function api(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error('No se pudo cargar la información. Intenta nuevamente.');
  return response.json();
}

function notice(target, message, error = false) {
  $(target).replaceChildren(node('p', message, error ? 'notice error' : 'notice'));
}
async function loadSummary() {
  try {
    const data = await api('/indice/resumen');
    $('pages').textContent = number(data.paginas);
    $('documents').textContent = number(data.documentos);
    $('categories').textContent = number(data.categorias.length);
    $('updated').textContent = date(data.ultima_revision);
    data.categorias.forEach((category) => {
      const option = node('option', category);
      option.value = category;
      $('category').append(option);
    });
    const portals = await api('/indice/portales');
    portals.forEach((portal) => {
      const option = node('option', portal);
      option.value = portal;
      $('portal').append(option);
    });
  } catch (error) {
    $('updated').textContent = 'No disponible';
  }
}

function pagination() {
  $('prev').disabled = offset === 0;
  $('next').disabled = offset + limit >= total;
  $('pagination').textContent = total ? `${offset + 1}–${Math.min(offset + limit, total)} de ${number(total)}` : '0 resultados';
}
async function loadPages() {
  const request = ++listRequest;
  notice('list', 'Cargando páginas…');
  $('count').textContent = 'Cargando…';
  $('prev').disabled = $('next').disabled = true;
  try {
    const data = await api('/indice/paginas?' + new URLSearchParams({
      ...filters,
      offset,
      limit
    }));
    if (request !== listRequest) return;
    total = data.total;
    $('count').textContent = `${number(total)} resultados`;
    $('list').replaceChildren();
    if (!total) notice('list', 'No se encontraron páginas. Prueba otra búsqueda o categoría.');
    currentItems = data.items;
    renderItems();
    pagination();
  } catch (error) {
    if (request !== listRequest) return;
    notice('list', error.message, true);
    $('count').textContent = 'Error de conexión';
    currentItems = [];
  }
}

function renderItems() {
  $('list').replaceChildren();
  if (!currentItems.length) {
    notice('list', 'No se encontraron páginas. Prueba otra búsqueda o categoría.');
    return;
  }
  if (tableView) {
    renderTable();
    return;
  }
  currentItems.forEach((page) => {
    const button = node('button', undefined, 'item' + (selected === page.url ? ' selected' : ''));
    button.type = 'button';
    button.dataset.url = page.url;
    button.setAttribute('aria-pressed', String(selected === page.url));
    let domain = page.url;
    try {
      domain = new URL(page.url).hostname;
    } catch {}
    button.append(node('span', domain, 'domain'), node('span', page.titulo || page.url, 'item-title'), node('span', page.extracto, 'excerpt'));
    page.categorias.forEach((category) => button.append(node('span', category, 'tag')));
    button.append(node('span', `${number(page.caracteres)} caracteres · ${page.documentos} documentos enlazados`, 'item-info'));
    button.addEventListener('click', () => showPage(page.url));
    $('list').append(button);
  });
}

function renderTable() {
  const table = node('table'),
    head = node('thead'),
    row = node('tr'),
    body = node('tbody');
  const columns = [
    ['Título', 'titulo'],
    ['Portal', 'portal'],
    ['Publicación', 'publicacion'],
    ['Revisión', 'recientes'],
    ['Enlaces', 'documentos'],
    ['Caracteres', 'caracteres'],
    ['Calidad', null]
  ];
  columns.forEach(([title, key]) => {
    const cell = node('th');
    if (key) {
      cell.setAttribute('aria-sort', filters.orden === key ? (filters.direccion === 'asc' ? 'ascending' : 'descending') : 'none');
      const button = node('button', title + (filters.orden === key ? (filters.direccion === 'asc' ? ' ↑' : ' ↓') : ''));
      button.addEventListener('click', () => {
        $('direction').value = filters.orden === key && filters.direccion === 'asc' ? 'desc' : 'asc';
        $('order').value = key;
        $('filters').requestSubmit();
      });
      cell.append(button);
    } else cell.textContent = title;
    row.append(cell);
  });
  head.append(row);
  table.append(head, body);
  currentItems.forEach((page) => {
    const row = node('tr'),
      title = node('td'),
      button = node('button', page.titulo || page.url, 'table-title');
    button.addEventListener('click', () => {
      showPage(page.url);
      document.querySelector('.reader').scrollIntoView({
        behavior: 'smooth'
      });
    });
    title.append(button);
    row.append(title);
    [page.portal, date(page.publicacion), date(page.checked), number(page.documentos), number(page.caracteres), page.calidad.map((key) => qualityLabels[key]).join(' · ') || 'Sin alertas'].forEach((value) => row.append(node('td', value)));
    body.append(row);
  });
  $('list').append(table);
}
async function showPage(url) {
  selected = url;
  const request = ++detailRequest;
  document.querySelectorAll('.item').forEach((item) => {
    item.classList.toggle('selected', item.dataset.url === url);
    item.setAttribute('aria-pressed', String(item.dataset.url === url));
  });
  notice('detail', 'Cargando contenido…');
  try {
    const data = await api('/indice/pagina?' + new URLSearchParams({
      url
    }));
    if (request !== detailRequest) return;
    const page = data.pagina,
      container = $('detail');
    container.replaceChildren(node('span', 'PÁGINA INDEXADA', 'eyebrow'), node('h2', page.titulo || url), link('Abrir fuente original ↗', url));
    container.append(node('div', `Revisión: ${date(data.checked)} · Publicación: ${date(page.fecha_publicacion)}`, 'meta'));
    (page.categorias || []).forEach((category) => container.append(node('span', category, 'tag')));
    container.append(node('h3', 'Contenido extraído'));
    let heading = '';
    (page.blocks || []).forEach((block) => {
      if (block.heading && block.heading !== heading) {
        heading = block.heading;
        container.append(node('h3', heading));
      }
      if (block.text) container.append(node('p', block.text));
    });
    if (!page.blocks?.length) container.append(node('p', 'Esta página no contiene bloques de texto guardados.'));
    container.append(node('h3', `Documentos enlazados (${data.documentos.length})`));
    if (data.documentos.length) container.append(node('p', 'Referencias encontradas en la página. El enlace no confirma que el contenido del archivo esté indexado.', 'document-note'));
    data.documentos.forEach((doc) => container.append(link(doc.nombre || doc.texto || doc.titulo || doc.url, doc.url, 'doc-link')));
    if (!data.documentos.length) container.append(node('p', 'Sin documentos enlazados.'));
    document.querySelector('.reader').scrollTop = 0;
    if (matchMedia('(max-width: 900px)').matches) document.querySelector('.reader').scrollIntoView({
      behavior: 'smooth'
    });
  } catch (error) {
    if (request === detailRequest) notice('detail', error.message, true);
  }
}
$('filters').addEventListener('submit', (event) => {
  event.preventDefault();
  $('filter-error').textContent = '';
  if ($('since').value && $('until').value && $('since').value > $('until').value) {
    $('filter-error').textContent = 'La fecha inicial debe ser anterior o igual a la final.';
    return;
  }
  offset = 0;
  filters = Object.fromEntries(Object.entries(filterInputs).map(([key, id]) => [key, $(id).value.trim()]).filter(([, value]) => value !== ''));
  loadPages();
});
Object.values(filterInputs).filter((id) => id !== 'query').forEach((id) => $(id).addEventListener('change', () => $('filters').requestSubmit()));
$('clear').addEventListener('click', () => {
  $('filters').reset();
  $('filters').requestSubmit();
});
for (const [id, value] of [
    ['cards-view', false],
    ['table-view', true]
  ]) {
  $(id).addEventListener('click', () => {
    tableView = value;
    document.querySelector('.workspace').classList.toggle('table-mode', value);
    $('cards-view').setAttribute('aria-pressed', String(!value));
    $('table-view').setAttribute('aria-pressed', String(value));
    renderItems();
  });
}
$('export').addEventListener('click', async () => {
  $('export').disabled = true;
  $('export-status').textContent = 'Preparando CSV…';
  try {
    const response = await fetch('/indice/exportar?' + new URLSearchParams(filters));
    if (!response.ok) throw new Error('No se pudo exportar. Intenta nuevamente.');
    const url = URL.createObjectURL(await response.blob());
    const anchor = node('a');
    anchor.href = url;
    anchor.download = 'indice-unamba.csv';
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    $('export-status').textContent = 'CSV exportado con todos los resultados filtrados.';
  } catch (error) {
    $('export-status').textContent = error.message;
  } finally {
    $('export').disabled = false;
  }
});
$('prev').addEventListener('click', () => {
  offset = Math.max(0, offset - limit);
  loadPages();
});
$('next').addEventListener('click', () => {
  offset += limit;
  loadPages();
});
loadSummary();
loadPages();
