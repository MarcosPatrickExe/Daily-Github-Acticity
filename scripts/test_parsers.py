#!/usr/bin/env python3
"""Testes das funções puras de parsing do coletor.

Não abrem navegador e não acessam a rede — rodam em segundos:

    python scripts/test_parsers.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from youtube_channel_scraper import (  # noqa: E402
    busca_profunda,
    maior_imagem,
    monta_resumo,
    normaliza_url_canal,
    parse_aba_videos,
    parse_numero,
    parse_sobre_canal,
    texto_de,
)

CASOS_NUMERO = [
    ("120 inscritos", 120),
    ("39.008 visualizações", 39_008),
    ("203 vídeos", 203),
    ("1.234.567 visualizações", 1_234_567),
    ("1,2 mil inscritos", 1_200),
    ("2,5 mi de visualizações", 2_500_000),
    ("3,4 bi", 3_400_000_000),
    ("10 mil", 10_000),
    ("1.2K subscribers", 1_200),
    ("1,234 views", 1_234),
    ('1 marcação "Gostei"', 1),
    ("2 Comentários", 2),
    ("Os comentários estão desativados.", None),
    ("", None),
    (None, None),
]

SOBRE_FALSO = {
    "x": {
        "aboutChannelViewModel": {
            "description": " Canal de testes \n",
            "country": "Brasil",
            "channelId": "UC123",
            "canonicalChannelUrl": "http://www.youtube.com/@teste",
            "subscriberCountText": "1,2 mil inscritos",
            "videoCountText": "42 vídeos",
            "viewCountText": "39.008 visualizações",
            "joinedDateText": {"content": "Inscreveu-se em 29 de dez. de 2021"},
            "links": [
                {"channelExternalLinkViewModel": {
                    "title": {"content": "Twitch"},
                    "link": {"content": "twitch.tv/teste"},
                }}
            ],
        }
    }
}

VIDEOS_FALSO = {
    "contents": [
        {"lockupViewModel": {
            "contentId": "abc123",
            "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
            "contentImage": {"thumbnailViewModel": {
                "image": {"sources": [{"url": "http://img/p.jpg", "width": 168},
                                      {"url": "http://img/g.jpg", "width": 336}]},
                "overlays": [{"thumbnailBottomOverlayViewModel": {
                    "badges": [{"thumbnailBadgeViewModel": {"text": "12:29"}}]}}],
            }},
            "metadata": {"lockupMetadataViewModel": {
                "title": {"content": "Vídeo de teste"},
                "metadata": {"contentMetadataViewModel": {"metadataRows": [
                    {"metadataParts": [
                        {"text": {"content": "12"}, "accessibilityLabel": "12 visualizações"},
                        {"text": {"content": "há 1 mês"}, "accessibilityLabel": "há 1 mês"},
                    ]}
                ]}},
            }},
        }}
    ]
}


def executa() -> int:
    falhas: list[str] = []

    def checa(nome: str, obtido, esperado) -> None:
        if obtido != esperado:
            falhas.append(f"{nome}: obtido {obtido!r}, esperado {esperado!r}")

    for texto, esperado in CASOS_NUMERO:
        checa(f"parse_numero({texto!r})", parse_numero(texto), esperado)

    checa("texto_de(content)", texto_de({"content": "oi"}), "oi")
    checa("texto_de(simpleText)", texto_de({"simpleText": "oi"}), "oi")
    checa("texto_de(runs)", texto_de({"runs": [{"text": "o"}, {"text": "i"}]}), "oi")
    checa("texto_de(None)", texto_de(None), None)

    checa("maior_imagem", maior_imagem([{"url": "a", "width": 1}, {"url": "b", "width": 9}]), "b")
    checa("maior_imagem(vazio)", maior_imagem([]), None)
    checa("busca_profunda", list(busca_profunda({"a": {"b": 1}, "c": [{"b": 2}]}, "b")), [1, 2])

    checa("normaliza_url_canal(@handle)",
          normaliza_url_canal("@teste"), "https://www.youtube.com/@teste")
    checa("normaliza_url_canal(com query)",
          normaliza_url_canal("https://youtube.com/@teste?si=abc"), "https://www.youtube.com/@teste")

    sobre = parse_sobre_canal(SOBRE_FALSO)
    checa("sobre.descricao", sobre.get("descricao"), "Canal de testes")
    checa("sobre.inscritos", sobre.get("inscritos"), 1_200)
    checa("sobre.total_videos", sobre.get("total_videos"), 42)
    checa("sobre.visualizacoes_totais", sobre.get("visualizacoes_totais"), 39_008)
    checa("sobre.pais", sobre.get("pais"), "Brasil")
    checa("sobre.criado_em_texto", sobre.get("criado_em_texto"), "Inscreveu-se em 29 de dez. de 2021")
    checa("sobre.links", sobre.get("links"), [{"titulo": "Twitch", "url": "twitch.tv/teste"}])

    videos = parse_aba_videos(VIDEOS_FALSO, 10)
    checa("videos.qtd", len(videos), 1)
    checa("videos.id", videos[0]["video_id"], "abc123")
    checa("videos.titulo", videos[0]["titulo"], "Vídeo de teste")
    checa("videos.url", videos[0]["url"], "https://www.youtube.com/watch?v=abc123")
    checa("videos.visualizacoes", videos[0]["visualizacoes"], 12)
    checa("videos.duracao", videos[0]["duracao"], "12:29")
    checa("videos.thumbnail", videos[0]["thumbnail"], "http://img/g.jpg")
    checa("videos.limite", len(parse_aba_videos(VIDEOS_FALSO, 0)), 0)

    resumo = monta_resumo([
        {"visualizacoes": 100, "curtidas": 8, "comentarios": 2, "titulo": "A", "url": "u1"},
        {"visualizacoes": 300, "curtidas": 10, "comentarios": 0, "titulo": "B", "url": "u2"},
        {"erro": "falhou"},
    ])
    checa("resumo.analisados", resumo["videos_analisados"], 2)
    checa("resumo.views", resumo["visualizacoes_soma"], 400)
    checa("resumo.curtidas", resumo["curtidas_soma"], 18)
    checa("resumo.media_views", resumo["visualizacoes_media"], 200.0)
    checa("resumo.engajamento", resumo["taxa_engajamento_pct"], 5.0)
    checa("resumo.mais_visto", resumo["video_mais_visto"]["titulo"], "B")
    checa("resumo.sem_videos", monta_resumo([])["taxa_engajamento_pct"], None)

    if falhas:
        print(f"❌ {len(falhas)} falha(s):")
        for falha in falhas:
            print(f"  - {falha}")
        return 1

    print("✅ Todos os testes de parsing passaram.")
    return 0


if __name__ == "__main__":
    raise SystemExit(executa())
