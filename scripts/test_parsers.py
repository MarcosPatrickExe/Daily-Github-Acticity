#!/usr/bin/env python3
"""Testes das funções puras de parsing do coletor.

Não abrem navegador e não acessam a rede — rodam em segundos:

    python scripts/test_parsers.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from youtube_channel_scraper import (  # noqa: E402
    busca_profunda,
    maior_imagem,
    monta_resumo,
    normaliza_url_canal,
    parse_aba_playlists,
    parse_aba_shorts,
    parse_aba_videos,
    parse_numero,
    parse_sobre_canal,
    resumo_por_tipo,
    texto_de,
)

# Recorte real do ytInitialData das abas /shorts, /playlists e /streams do canal,
# podado dos campos de tracking. Garante que os parsers batem com a estrutura
# que o YouTube realmente entrega, não com uma inventada aqui.
ABAS_REAIS = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "abas_canal.json").read_text(encoding="utf-8")
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
    ("1.234 Comentários", 1_234),
    # Regressão: o cabeçalho dos comentários aparece sem número enquanto a
    # contagem não carrega. Não pode ser confundido com uma contagem válida.
    ("Comentários", None),
    ("Seja o primeiro a comentar", None),
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

    # --- abas Shorts, playlists e lives (dados reais) ---------------------
    shorts = parse_aba_shorts(ABAS_REAIS["shorts"], 10)
    checa("shorts.qtd", len(shorts), 2)
    checa("shorts.id", shorts[0]["video_id"], "yF46_VMGp_8")
    checa("shorts.tipo", shorts[0]["tipo"], "short")
    checa("shorts.titulo", shorts[0]["titulo"],
          "QUASE QUE VACILO NO FINAL 😂 LIVE AMANHÃ(12-07) ÀS 19H")
    checa("shorts.visualizacoes", shorts[0]["visualizacoes"], 862)
    checa("shorts.abreviado (1,2 mil)", shorts[1]["visualizacoes"], 1_200)
    checa("shorts.url é watch?v=", shorts[0]["url"],
          "https://www.youtube.com/watch?v=yF46_VMGp_8")
    checa("shorts.thumbnail", bool(shorts[0]["thumbnail"]), True)
    checa("shorts.limite", len(parse_aba_shorts(ABAS_REAIS["shorts"], 1)), 1)
    checa("shorts.limite zero", parse_aba_shorts(ABAS_REAIS["shorts"], 0), [])

    playlists = parse_aba_playlists(ABAS_REAIS["playlists"], 10)
    checa("playlists.qtd", len(playlists), 3)
    checa("playlists.id", playlists[0]["playlist_id"], "PLn1eC3WaPHoYCZbN3dD6BUodnVNnQioXA")
    checa("playlists.titulo", playlists[0]["titulo"], "Elden Ring NIGHTREIGN")
    checa("playlists.url", playlists[0]["url"],
          "https://www.youtube.com/playlist?list=PLn1eC3WaPHoYCZbN3dD6BUodnVNnQioXA")
    # O YouTube escreve "Um vídeo" por extenso quando a playlist tem só um.
    checa("playlists.'Um vídeo' vira 1", playlists[0]["total_videos"], 1)
    checa("playlists.'2 vídeos' vira 2", playlists[2]["total_videos"], 2)
    checa("playlists.ignora vídeos soltos", parse_aba_playlists(VIDEOS_FALSO, 10), [])

    lives = parse_aba_videos(ABAS_REAIS["streams"], 10, tipo="live")
    checa("lives.qtd", len(lives), 1)
    checa("lives.tipo", lives[0]["tipo"], "live")
    checa("lives.visualizacoes", lives[0]["visualizacoes"], 25)
    checa("lives.publicado", lives[0]["publicado_texto"], "Transmitido há 12 dias")
    checa("videos.tipo padrão", parse_aba_videos(VIDEOS_FALSO, 10)[0]["tipo"], "video")
    # A aba de playlists também usa lockupViewModel: não pode virar "vídeo".
    checa("videos.ignora playlists", parse_aba_videos(ABAS_REAIS["playlists"], 10), [])

    # --- resumo por formato ----------------------------------------------
    por_tipo = resumo_por_tipo([
        {"tipo": "video", "visualizacoes": 100, "curtidas": 5, "comentarios": 0},
        {"tipo": "short", "visualizacoes": 900, "curtidas": 40, "comentarios": 5},
        {"tipo": "short", "visualizacoes": 100, "curtidas": 10, "comentarios": 0},
        {"tipo": "live", "erro": "timeout"},
    ])
    checa("por_tipo.formatos", sorted(por_tipo), ["short", "video"])
    checa("por_tipo.short.qtd", por_tipo["short"]["quantidade"], 2)
    checa("por_tipo.short.views", por_tipo["short"]["visualizacoes_soma"], 1_000)
    checa("por_tipo.short.media", por_tipo["short"]["visualizacoes_media"], 500.0)
    checa("por_tipo.short.engajamento", por_tipo["short"]["taxa_engajamento_pct"], 5.5)
    checa("por_tipo.sem tipo vira vídeo", resumo_por_tipo([{"visualizacoes": 1}])["video"]["quantidade"], 1)
    checa("por_tipo.vazio", resumo_por_tipo([]), {})

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
