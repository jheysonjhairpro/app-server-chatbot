import csv
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.explorer import router


class ExplorerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name) / 'state.sqlite3'
        with sqlite3.connect(path) as db:
            db.executescript('CREATE TABLE pages(url TEXT,title TEXT,checked TEXT,payload TEXT,hash TEXT,status TEXT); CREATE TABLE links(source TEXT,target TEXT,document INTEGER);')
            for i, (title, publication, content) in enumerate([
                ('Titulo', '2026-01-01', 'Texto compartido'),
                ('Titulo', None, 'Texto compartido'),
                ('=1+1', '2026-02-01', 'Otro contenido'),
            ]):
                payload = json.dumps({'categorias': ['Noticias'], 'fecha_publicacion': publication, 'blocks': [{'text': content}]})
                db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?)', (f'https://example.org/{i}', title, '2026-03-01T12:00:00Z', payload, 'hash', 'indexed'))
            db.execute('INSERT INTO links VALUES(?,?,1)', ('https://example.org/0', 'https://example.org/file.PDF?download=1'))
        db.close()
        app = FastAPI()
        app.state.services = SimpleNamespace(settings=SimpleNamespace(state_path=path))
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.directory.cleanup()

    def test_combined_filters_and_dates(self):
        result = self.client.get('/indice/paginas', params={'portal': 'example.org', 'documentos': 'pdf', 'desde': '2026-01-01', 'hasta': '2026-01-01', 'calidad': 'titulo_repetido'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['total'], 1)
        self.assertEqual(self.client.get('/indice/paginas?desde=2027-01-01&hasta=2026-01-01').status_code, 422)
        self.assertEqual(self.client.get('/indice/paginas?desde=invalid').status_code, 422)

    def test_quality_search_and_sort(self):
        self.assertEqual(self.client.get('/indice/paginas?calidad=contenido_repetido').json()['total'], 2)
        self.assertEqual(self.client.get('/indice/paginas?calidad=sin_fecha').json()['total'], 1)
        self.assertEqual(self.client.get('/indice/paginas?q=compartido&campo=titulo').json()['total'], 0)
        self.assertEqual(self.client.get('/indice/paginas?q=compartido&campo=contenido').json()['total'], 2)
        result = self.client.get('/indice/paginas?orden=documentos&direccion=desc&limit=1&offset=0').json()
        self.assertEqual(result['total'], 3)
        self.assertEqual(result['items'][0]['documentos'], 1)

    def test_export_all_results_and_formula_protection(self):
        response = self.client.get('/indice/exportar?portal=example.org')
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(len(rows), 4)
        self.assertIn("'=1+1", [row[0] for row in rows])
        filtered = self.client.get('/indice/exportar?documentos=pdf')
        self.assertEqual(len(list(csv.reader(io.StringIO(filtered.content.decode('utf-8-sig')), delimiter=';'))), 2)


if __name__ == '__main__':
    unittest.main()
