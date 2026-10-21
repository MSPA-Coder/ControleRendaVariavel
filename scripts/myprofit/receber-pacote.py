"""Recebe UM pacote por POST em 127.0.0.1:8766, grava no arquivo dado e encerra.

Traz a coleta do myProfit (``coletar.js``) da aba do navegador embutido para um
arquivo local. O navegador embutido bloqueia download e ``fetch`` para o
``localhost``, mas aceita a navegação de um formulário; por isso a aba envia o
pacote assim (ver ``docs/conciliacao-mensal.md``). Aceita só a origem do myProfit,
um pedido, e nada vai na URL.

    py scripts/myprofit/receber-pacote.py C:/caminho/coleta.json
"""
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

DESTINO = Path(sys.argv[1])
ORIGENS = {"https://myprofitweb.com", "null"}
LIMITE = 8 * 1024 * 1024


class Receptor(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.headers.get("Origin", "null") not in ORIGENS:
            self.send_response(403)
            self.end_headers()
            return
        tamanho = int(self.headers.get("Content-Length", "0"))
        if not 0 < tamanho <= LIMITE:
            self.send_response(413)
            self.end_headers()
            return
        corpo = self.rfile.read(tamanho)
        if "x-www-form-urlencoded" in (self.headers.get("Content-Type") or ""):
            corpo = parse_qs(corpo.decode("utf-8"))["pacote"][0].encode("utf-8")
        DESTINO.write_bytes(corpo)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"pacote gravado: {len(corpo)} bytes".encode())
        self.server.recebido = True

    def log_message(self, *args):
        pass


servidor = HTTPServer(("127.0.0.1", 8766), Receptor)
servidor.recebido = False
while not servidor.recebido:
    servidor.handle_request()
print("gravado", DESTINO, DESTINO.stat().st_size)
