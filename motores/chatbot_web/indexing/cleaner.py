import re
import hashlib
import unicodedata


def clean(text):
    return "\n".join(
        re.sub(r"\s+", " ", line).strip()
        for line in unicodedata.normalize("NFKC", text).splitlines()
        if line.strip()
    )


def content_hash(text):
    return hashlib.sha256(clean(text).encode()).hexdigest()


def folded(text):
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", text.lower())
        if not unicodedata.combining(c)
    )
