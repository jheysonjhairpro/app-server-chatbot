"""Piezas compartidas por todos los modos"""
import contextvars
import queue
import sys
import threading

from flask import Response, stream_with_context

_salida_actual = contextvars.ContextVar("salida_actual", default=None)
_stdout_original = sys.stdout


class Salida:
    """Reemplaza stdout y manda cada fragmento a una cola."""

    def __init__(self, q):
        self.q = q

    def write(self, s):
        if s:
            self.q.put(s)
        return len(s)

    def flush(self):
        pass


class SalidaPorHilo:
    """Dirige print() a la cola de la petición actual sin bloquear otras."""

    def write(self, s):
        salida = _salida_actual.get()
        if salida is None:
            return _stdout_original.write(s)
        return salida.write(s)

    def flush(self):
        salida = _salida_actual.get()
        if salida is None:
            return _stdout_original.flush()
        return salida.flush()


if not isinstance(sys.stdout, SalidaPorHilo):
    sys.stdout = SalidaPorHilo()


def transmitir(tarea):
    """Ejecuta tarea() y envía al cliente todo lo que imprima, en vivo."""
    q = queue.Queue()

    def trabajo():
        token = _salida_actual.set(Salida(q))
        try:
            tarea()
        except Exception as exc:
            print(f"Error en la consulta: {exc}")
        finally:
            _salida_actual.reset(token)
            q.put(None)

    threading.Thread(target=trabajo, daemon=True).start()

    def generar():
        while (item := q.get()) is not None:
            yield item

    return Response(stream_with_context(generar()),
                    mimetype="text/plain; charset=utf-8",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
