#!/usr/bin/env python3
"""Reconstrói os CSVs de histórico a partir dos relatórios JSON já gravados.

Os CSVs são derivados: cada `AAAA-MM-DD.json` do diretório de relatórios contém
tudo que eles guardam. Serve para semear o histórico com coletas antigas e para
recuperá-lo caso o arquivo se perca ou fique inconsistente.

    python scripts/reconstroi_historico.py [--output-dir reports/youtube]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import historico  # noqa: E402

RE_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="reports/youtube",
                        help="Diretório dos relatórios (padrão: reports/youtube).")
    args = parser.parse_args()

    destino = Path(args.output_dir)
    if not destino.is_dir():
        print(f"ERRO: diretório não encontrado: {destino}", file=sys.stderr)
        return 1

    # `latest.json` é cópia de um dos relatórios diários — incluí-lo duplicaria a linha.
    arquivos = sorted(a for a in destino.glob("*.json") if RE_DATA.match(a.stem))
    if not arquivos:
        print(f"Nenhum relatório diário encontrado em {destino}.")
        return 1

    linhas_canal: list[dict] = []
    linhas_videos: list[dict] = []
    for arquivo in arquivos:
        try:
            relatorio = json.loads(arquivo.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as erro:
            print(f"  ! {arquivo.name}: ignorado ({erro})", file=sys.stderr)
            continue
        data = arquivo.stem
        linhas_canal.append(historico.linha_do_canal(relatorio, data))
        linhas_videos.extend(historico.linhas_dos_videos(relatorio, data))
        print(f"  + {arquivo.name}")

    caminho_canal = destino / historico.ARQUIVO_CANAL
    caminho_videos = destino / historico.ARQUIVO_VIDEOS
    historico.grava_csv(
        caminho_canal, historico.COLUNAS_CANAL, historico.upsert([], linhas_canal, ("data",))
    )
    historico.grava_csv(
        caminho_videos,
        historico.COLUNAS_VIDEOS,
        historico.upsert([], linhas_videos, ("data", "video_id")),
    )

    print(f"\n{caminho_canal}: {len(linhas_canal)} coletas")
    print(f"{caminho_videos}: {len(linhas_videos)} linhas de vídeo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
