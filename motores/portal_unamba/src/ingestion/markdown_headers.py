"""Secciones Markdown con jerarquía completa y texto original conservado."""

import re

from llama_index.core.node_parser.node_utils import build_nodes_from_splits

ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$")
SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def markdown_header_nodes(documents):
    """Divide por H1–H6 y Setext, ignorando encabezados dentro de código/YAML.

    Cada sección incluye su propio encabezado en header_path y header_N.
    Los encabezados repetidos producen secciones independientes.
    """
    nodes = []
    for document in documents:
        lines = document.text.splitlines(keepends=True)
        headings = {}
        fence = None
        frontmatter = bool(lines and lines[0].strip() == "---")
        # Solo se trata como YAML si hay un delimitador de cierre.
        yaml_end = next((i for i in range(1, len(lines))
                         if lines[i].strip() in {"---", "..."}), None) if frontmatter else None
        index = 0
        while index < len(lines):
            line = lines[index].rstrip("\r\n")
            if yaml_end is not None and index <= yaml_end:
                index += 1
                continue
            if fence:
                if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(fence[1]) + r",}[ \t]*", line):
                    fence = None
                index += 1
                continue
            match = FENCE.match(line)
            if match and not (match[1][0] == "`" and "`" in match[2]):
                fence = (match[1][0], len(match[1]))
                index += 1
                continue
            match = ATX.match(line)
            if match:
                title = re.sub(r"[ \t]+#+[ \t]*$", "", match[2] or "").strip()
                headings[index] = (len(match[1]), title)
            elif line.strip() and not line.startswith(("    ", "\t")) and index + 1 < len(lines):
                underline = SETEXT.match(lines[index + 1].rstrip("\r\n"))
                if underline and not re.match(r"^ {0,3}(?:[-+*>]|\d+[.)])\s", line):
                    headings[index] = (1 if underline[1][0] == "=" else 2, line.strip())
                    index += 1
            index += 1

        starts = sorted({0, *headings}) if lines else []
        stack = []
        section_index = 0
        for offset, start in enumerate(starts):
            if start in headings:
                level, title = headings[start]
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, title))
            end = starts[offset + 1] if offset + 1 < len(starts) else len(lines)
            text = "".join(lines[start:end])
            if not text.strip():
                continue
            node = build_nodes_from_splits([text], document)[0]
            node.metadata = dict(document.metadata)
            # Evita heredar encabezados de metadatos previos.
            for key in list(node.metadata):
                if re.fullmatch(r"header_[1-6]", key):
                    node.metadata.pop(key)
            node.metadata.update({f"header_{level}": title for level, title in stack})
            node.metadata["header_path"] = " > ".join(title for _, title in stack)
            node.metadata["section_index"] = section_index
            node.metadata["section_id"] = node.node_id
            section_index += 1
            nodes.append(node)
    return nodes
