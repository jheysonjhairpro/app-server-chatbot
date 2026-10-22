"""Enlaces de procedencia tomados de metadatos, nunca del texto generado."""
import os
import sys
from urllib.parse import urlsplit


def web_url(value: str) -> str:
    if not value or any(ord(char) < 33 or ord(char) == 127 for char in value):
        return ""
    try:
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return ""


def source_link(url: str) -> str:
    url = web_url(url)
    if not url:
        return ""
    # OSC 8 permite pulsar la URL en Windows Terminal y VS Code.
    if sys.stdout.isatty() and (os.getenv("WT_SESSION") or os.getenv("TERM_PROGRAM") == "vscode"):
        return f"\033]8;;{url}\033\\{url}\033]8;;\033\\"
    return url


def print_sources(sources, show_excerpts: bool = False) -> None:
    if not sources:
        if show_excerpts:
            print("\nFuentes: ninguna.")
        return
    print("\nFuentes consultadas:")
    for index, source in enumerate(sources, start=1):
        number = source.number or index
        url = web_url(source.url)
        title = source.title or ("Página de origen" if url else source.file)
        print(f"\n[{number}] {title}")
        if url:
            print(source_link(url))
        else:
            print(f"Archivo local: {source.file} (sin URL de origen)")
        if show_excerpts:
            print("   " + source.excerpt.replace("\n", "\n   "))
    print("Usa /fuente N para abrir una fuente en el navegador.")
