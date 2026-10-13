"""Compatibilidad con imports anteriores del streaming de documentos.

La implementación vive en streaming_stdout; importar este módulo no instala
un segundo capturador de stdout.
"""
from streaming_stdout import Salida, SalidaPorHilo, transmitir

__all__ = ["Salida", "SalidaPorHilo", "transmitir"]
