import asyncio
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from config import Settings
from indexing.chroma_store import ChromaStore, document_id
from main import Services
from rag.retrieval import RAG
from storage.state import State


class RetrievalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = State.__new__(State)
        self.state.db = sqlite3.connect(':memory:')
        self.state.db.row_factory = sqlite3.Row
        self.state.db.executescript('''
            CREATE TABLE pages(url TEXT, title TEXT, hash TEXT, payload TEXT, status TEXT);
            CREATE TABLE meta(key TEXT, value TEXT);
            INSERT INTO meta VALUES('version', '1');
        ''')
        self.records = []
        for name, category, status in [
            ('becas', 'Becas', 'indexed'),
            ('admision', 'Admisión', 'indexed'),
            ('pendiente', 'Becas', 'pending'),
            ('eliminada', 'Becas', 'gone'),
            ('huerfana', 'Becas', None),
        ]:
            url = f'https://example.org/{name}'
            if status:
                self.state.db.execute('INSERT INTO pages VALUES(?,?,?,?,?)',
                                      (url, name, None if status == 'pending' else 'hash', '{}', status))
            self.records.append({'id': document_id(url) + ':0',
                                 'texto': name + ' informacion institucional ' + category,
                                 'similarity': 0.8,
                                 'metadata': {'url': url, 'titulo': name, 'categoria': category}})
        self.store = SimpleNamespace(query=Mock(side_effect=self.query))
        self.rag = RAG(Settings(), self.state, self.store,
                       SimpleNamespace(identity='test', embed=AsyncMock(return_value=[[1.0]])),
                       asyncio.Lock())

    def tearDown(self):
        self.state.db.close()

    def query(self, vector, k, category=None, document_ids=None):
        return [r for r in self.records
                if (not category or r['metadata']['categoria'] == category)
                and (document_ids is None or document_id(r['metadata']['url']) in document_ids)][:k]

    async def test_excludes_orphan_pending_and_gone_documents(self):
        result = await self.rag.search('consulta general')
        urls = {r['metadata']['url'] for r in result['fragmentos']}
        self.assertEqual(urls, {'https://example.org/becas', 'https://example.org/admision'})

    async def test_explicit_category_applies_to_title_matches(self):
        result = await self.rag.search('admision', 'Becas')
        self.assertEqual({r['metadata']['categoria'] for r in result['fragmentos']}, {'Becas'})
        self.assertTrue(all(call.args[2] == 'Becas' for call in self.store.query.call_args_list))

    async def test_explicit_category_with_no_results_does_not_fall_back(self):
        result = await self.rag.search('admision', 'Posgrado')
        self.assertEqual(result['fragmentos'], [])
        self.assertEqual(result['categoria_filtro'], 'Posgrado')

    async def test_inferred_category_can_fall_back_with_catalog_restriction(self):
        result = await self.rag.search('biblioteca')
        self.assertIsNone(result['categoria_filtro'])
        self.assertTrue(result['fragmentos'])
        self.assertTrue(all(call.args[3] for call in self.store.query.call_args_list))

    async def test_empty_catalog_returns_no_context(self):
        self.state.db.execute('DELETE FROM pages')
        result = await self.rag.search('consulta general')
        self.assertEqual(result['contexto'], '')


class StoreTests(unittest.TestCase):
    def test_empty_allowlist_never_queries_collection(self):
        store = ChromaStore.__new__(ChromaStore)
        store.collection = Mock()
        self.assertEqual(store.query([1.0], 8, document_ids=[]), [])
        store.collection.query.assert_not_called()


class HealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_reflects_chat_model_availability(self):
        svc = Services.__new__(Services)
        svc.settings = Settings()
        svc.state = SimpleNamespace(version=1)
        svc.store = SimpleNamespace(collection=SimpleNamespace(count=lambda: 10))
        for available in (False, True):
            with self.subTest(chat_available=available):
                svc.prepare = AsyncMock(return_value={'chat_available': available})
                result = await svc.health()
                self.assertEqual(result['ok'], available)
                self.assertEqual(result['chroma']['fragmentos'], 10)


if __name__ == '__main__':
    unittest.main()
