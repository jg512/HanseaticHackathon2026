"""Lädt html2markdown (github.com/JohannesKaufmann/html-to-markdown) ins Image und prüft die Prüfsumme.

Die Internet-Stufe der Suche liest Webseiten nur als Markdown, nie als rohes HTML.
"""

import hashlib
import io
import platform
import tarfile
import urllib.request

VERSION = "2.5.2"
SHA256 = {
    "x86_64": "337588fd2f15b87869f1ad608506ab6b9ff5f65cf4cb33509ae07ab2852d5f80",
    "arm64": "4fc380e0fd86c333e2168bb703eaddbfbbefe19c93be144ae6a5a239d50042bc",
}

arch = {"x86_64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}[platform.machine()]
url = (f"https://github.com/JohannesKaufmann/html-to-markdown/releases/download/"
       f"v{VERSION}/html-to-markdown_Linux_{arch}.tar.gz")
daten = urllib.request.urlopen(url, timeout=120).read()
if hashlib.sha256(daten).hexdigest() != SHA256[arch]:
    raise SystemExit(f"Prüfsumme von {url} stimmt nicht.")
with tarfile.open(fileobj=io.BytesIO(daten)) as tar:
    tar.extract("html2markdown", "/usr/local/bin", filter="data")
