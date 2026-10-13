"""Contratos del servidor compartido, sin abrir índices ni necesitar Ollama.

Ejecutar desde la raíz: venv/Scripts/python.exe -m unittest discover -s tests -v
"""

import importlib
import json
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

import httpx


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "servidor"))
docs_stub = ModuleType("modos.docs")
docs_stub.responder = Mock()
with patch.dict(sys.modules, {"modos.docs": docs_stub}):
    server = importlib.import_module("app")
    integrated = importlib.import_module("app_integrada")
web = importlib.import_module("modos.web")


class ServerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()
        self.headers = {"X-Token": server.config.TOKEN}
        docs_stub.responder.reset_mock(side_effect=True)

    def backend(self, handler):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return patch.object(web.httpx, "Client", return_value=client)

    def test_single_web_route_and_shared_application(self):
        self.assertIs(server.app, integrated.app)
        routes = [rule for rule in server.app.url_map.iter_rules()
                  if rule.rule == "/chat/web" and "POST" in rule.methods]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].endpoint, "chatbot_web.chat_web")

    def test_compatibility_imports_share_implementation_and_pdf_settings(self):
        legacy = importlib.import_module("comun")
        streaming = importlib.import_module("streaming_stdout")
        pdf_config = importlib.import_module("config_docs")
        self.assertIs(legacy.transmitir, streaming.transmitir)
        self.assertIs(legacy.SalidaPorHilo, streaming.SalidaPorHilo)
        self.assertIs(sys.stdout.__class__, streaming.SalidaPorHilo)
        self.assertEqual(server.config.CHROMA_DIR, pdf_config.CHROMA_DIR)
        self.assertEqual(server.config.COLECCION_DOCS, pdf_config.COLECCION_DOCS)

    def test_authentication_protects_both_modes_and_web_health(self):
        self.assertEqual(self.client.get("/salud").status_code, 200)
        for headers in ({}, {"X-Token": "invalid-test-token"}):
            for route in ("/chat/docs", "/chat/web"):
                self.assertEqual(self.client.post(route, json={"message": "hola"},
                                                 headers=headers).status_code, 403)
            self.assertEqual(self.client.get("/salud/web", headers=headers).status_code, 403)

    def test_docs_keeps_message_session_and_stdout_stream(self):
        docs_stub.responder.side_effect = lambda text, sid: print(f"{sid}: {text}")
        response = self.client.post("/chat/docs", json={"message": " /pdfs ", "session": "test"},
                                    headers=self.headers, buffered=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), "test: /pdfs\n")
        docs_stub.responder.assert_called_once_with("/pdfs", "test")

    def test_web_validation_does_not_call_backend(self):
        with patch.object(web.httpx, "Client") as client:
            for body in (None, [], {}, {"message": 3}, {"message": " "}, {"message": "a"},
                         {"message": "a" * 2001}):
                response = self.client.post("/chat/web", json=body, headers=self.headers)
                self.assertEqual(response.status_code, 400)
            client.assert_not_called()

    def test_web_uses_native_validation_and_converts_stream(self):
        question = "a" * 1500
        def handle(request):
            self.assertEqual(request.url.path, "/rag/preguntar")
            self.assertEqual(json.loads(request.content), {"pregunta": question, "streaming": True})
            events = [{"tipo": "token", "texto": "Respuesta"},
                      {"tipo": "fin", "fuentes": [{"titulo": "Fuente", "url": "https://example.org"}],
                       "metricas": {"motivo_fin": "length"}}]
            return httpx.Response(200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events))
        with self.backend(handle):
            response = self.client.post("/chat/web", json={"message": " " + question + " "},
                                        headers=self.headers, buffered=True)
        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertTrue(text.startswith("Respuesta\n\nFuentes consultadas:"))
        self.assertIn("Fuente: https://example.org", text)
        self.assertIn("límite de tokens", text)

    def test_web_backend_unavailable(self):
        def handle(request):
            raise httpx.ConnectError("Unavailable", request=request)
        with self.backend(handle):
            response = self.client.post("/chat/web", json={"message": "consulta"}, headers=self.headers)
        self.assertEqual(response.status_code, 503)

    def test_web_stream_error_and_incomplete_response(self):
        for event, expected in [({"tipo": "error", "detalle": "fallo de prueba"}, "fallo de prueba"),
                                ({"tipo": "token", "texto": "parcial"}, "antes de completar")]:
            with self.subTest(event=event), self.backend(
                lambda request: httpx.Response(200, text="data: " + json.dumps(event) + "\n\n")
            ):
                response = self.client.post("/chat/web", json={"message": "consulta"},
                                            headers=self.headers, buffered=True)
                self.assertIn(expected, response.get_data(as_text=True))

    def test_web_health_preserves_backend_response(self):
        with self.backend(lambda request: httpx.Response(200, json={"ok": True, "chroma": {"fragmentos": 7}})):
            response = self.client.get("/salud/web", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"ok": True, "chroma": {"fragmentos": 7}})


if __name__ == "__main__":
    unittest.main()
