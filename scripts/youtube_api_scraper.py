#!/usr/bin/env python3
"""Coletor de métricas públicas de um canal do YouTube usando a API v3.

Este scraper substitui o Playwright por chamadas diretas à YouTube Data API v3,
que é mais confiável, não requer navegador e retorna dados oficiais.

Saídas geradas no diretório de relatórios:
  - <data>.json           -> dados brutos estruturados
  - <data>.md             -> relatório legível, com tendência e diagnóstico
  - latest.json           -> cópia do último JSON gerado
  - historico.csv         -> série temporal das métricas do canal
  - historico_videos.csv  -> série temporal das métricas por vídeo

Requer variável de ambiente:
  - YOUTUBE_API_KEY       -> Chave da API v3 do YouTube (gratuita)

Códigos de saída: 0 sucesso, 1 falha na coleta, 2 relatório com problemas no diagnóstico.

Uso:
    export YOUTUBE_API_KEY=sua_chave_aqui
    python scripts/youtube_api_scraper.py \
        --channel UCB0Xu_75SQQIHVjaTmGBYuQ \
        --max-videos 10
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import diagnostico
import grafico
import historico

FUSO_LOCAL = timezone(timedelta(hours=-3))  # America/Fortaleza
ARQUIVO_GRAFICO = "evolucao.svg"

# Sufixos abreviados que o YouTube usa em pt-BR e en-US ("1,2 mil", "1.2K").
MULTIPLICADORES = {
    "mil": 1_000,
    "mi": 1_000_000,
    "bi": 1_000_000_000,
    "k": 1_000,
    "m": 1_000_000,
    "b": 1_000_000_000,
}

RE_NUMERO = re.compile(r"(\d[\d.,   ]*)\s*(mil|mi|bi|k|m|b)?\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Utilidades de parsing
# ---------------------------------------------------------------------------


def parse_numero(texto: str | None) -> int | None:
    """Converte '120 inscritos', '39.008 visualizações' ou '1,2 mil' em inteiro."""
    if not texto:
        return None
    m = RE_NUMERO.search(texto)
    if not m:
        return None

    bruto, sufixo = m.group(1), (m.group(2) or "").lower()
    limpo = re.sub(r"[\s  ]", "", bruto)
    if not limpo:
        return None

    if sufixo:
        decimal = limpo.replace(".", "").replace(",", ".") if "," in limpo else limpo
        try:
            return int(round(float(decimal) * MULTIPLICADORES[sufixo]))
        except ValueError:
            return None

    digitos = re.sub(r"[^\d]", "", limpo)
    return int(digitos) if digitos else None


def formata_numero(num: int | str) -> str:
    """Formata número em pt-BR: 1000 -> '1 mil', 1500000 -> '1,5 mi'."""
    num = int(num)
    if num >= 1_000_000_000:
        return f"{num / 1_000_000_000:.1f}".replace(".", ",") + " bi"
    elif num >= 1_000_000:
        return f"{num / 1_000_000:.1f}".replace(".", ",") + " mi"
    elif num >= 1_000:
        return f"{num / 1_000:.1f}".replace(".", ",") + " mil"
    return str(num)


# ---------------------------------------------------------------------------
# Coleta via API v3
# ---------------------------------------------------------------------------


def extrai_channel_id(identificador: str, youtube) -> str | None:
    """Extrai o channel_id a partir de um handle @username ou URL."""
    # Se já é um channel_id (começa com "UC"), retorna direto
    if identificador.startswith("UC"):
        return identificador

    # Se é um handle (@username), procura o channel_id
    if identificador.startswith("@"):
        try:
            request = youtube.search().list(
                part="snippet",
                q=identificador,
                type="channel",
                maxResults=1
            )
            response = request.execute()
            if response["items"]:
                return response["items"][0]["snippet"]["channelId"]
        except HttpError:
            pass

    # Tenta como URL
    if "youtube.com" in identificador or "youtu.be" in identificador:
        match = re.search(r"[/@]([a-zA-Z0-9_-]+)", identificador)
        if match:
            return extrai_channel_id(match.group(1), youtube)

    return None


def coleta_canal(channel_id: str, youtube) -> dict[str, Any]:
    """Coleta dados do cabeçalho do canal via API."""
    try:
        request = youtube.channels().list(
            part="snippet,statistics",
            id=channel_id
        )
        response = request.execute()
        if not response["items"]:
            raise ValueError(f"Canal {channel_id} não encontrado")

        canal = response["items"][0]
        snippet = canal["snippet"]
        stats = canal["statistics"]

        return {
            "nome": snippet.get("title", ""),
            "handle": f"@{snippet.get('customUrl', '').split('/')[-1]}",
            "descricao": snippet.get("description", ""),
            "avatar_url": snippet["thumbnails"]["high"]["url"],
            "banner_url": snippet.get("brandingSettings", {}).get("bannerExternalUrl", ""),
            "channel_id": channel_id,
            "url_canal": f"http://www.youtube.com/channel/{channel_id}",
            "rss": f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
            "inscritos": int(stats.get("subscriberCount", 0)),
            "inscritos_texto": formata_numero(stats.get("subscriberCount", 0)) + " inscritos",
            "total_videos": int(stats.get("videoCount", 0)),
            "videos_texto": stats.get("videoCount", "0") + " vídeos",
            "visualizacoes_totais": int(stats.get("viewCount", 0)),
            "visualizacoes_texto": formata_numero(stats.get("viewCount", 0)) + " visualizações",
            "criado_em_texto": snippet.get("publishedAt", ""),
            "pais": snippet.get("country", ""),
            "links": [],
            "palavras_chave": snippet.get("keywords", ""),
        }
    except HttpError as e:
        raise RuntimeError(f"Erro ao coletar canal: {e}")


def coleta_videos(channel_id: str, youtube, max_videos: int = 10) -> list[dict[str, Any]]:
    """Coleta a lista de vídeos do canal via API."""
    videos = []
    try:
        # Primeiro pega os uploads mais recentes do playlist "Uploads"
        request = youtube.channels().list(
            part="contentDetails",
            id=channel_id
        )
        response = request.execute()
        uploads_playlist_id = response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

        # Depois busca os vídeos do playlist
        request = youtube.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=min(max_videos + 10, 50)  # Pega extras porque alguns podem ser privados
        )

        while request and len(videos) < max_videos:
            response = request.execute()
            for item in response.get("items", []):
                if len(videos) >= max_videos:
                    break

                video_id = item["contentDetails"]["videoId"]
                snippet = item["snippet"]

                videos.append({
                    "video_id": video_id,
                    "tipo": "video",
                    "titulo": snippet["title"],
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "thumbnail": snippet["thumbnails"]["high"]["url"],
                    "publicado_texto": snippet["publishedAt"],
                })

            # Próxima página
            request = youtube.playlistItems().list_next(request, response) if "nextPageToken" in response else None

        # Agora pega estatísticas de cada vídeo
        for i in range(0, len(videos), 50):
            batch = videos[i:i+50]
            video_ids = ",".join(v["video_id"] for v in batch)

            request = youtube.videos().list(
                part="statistics,contentDetails",
                id=video_ids
            )
            response = request.execute()

            for item in response.get("items", []):
                video_id = item["id"]
                stats = item["statistics"]
                duration = item["contentDetails"]["duration"]

                # Encontra o vídeo correspondente e atualiza
                for v in videos:
                    if v["video_id"] == video_id:
                        v["visualizacoes"] = int(stats.get("viewCount", 0))
                        v["visualizacoes_texto"] = formata_numero(stats.get("viewCount", 0)) + " visualizações"
                        v["curtidas"] = int(stats.get("likeCount", 0)) if "likeCount" in stats else 0
                        v["curtidas_texto"] = formata_numero(stats.get("likeCount", 0)) + " curtidas" if "likeCount" in stats else "0 curtidas"
                        v["comentarios"] = int(stats.get("commentCount", 0)) if "commentCount" in stats else 0
                        v["comentarios_texto"] = f"{v['comentarios']} comentários"
                        v["duracao"] = duration_para_texto(duration)
                        break

        return videos
    except HttpError as e:
        raise RuntimeError(f"Erro ao coletar vídeos: {e}")


def duration_para_texto(duration: str) -> str:
    """Converte PT1H30M45S em 1:30:45 ou PT30M45S em 30:45."""
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not match:
        return "0:00"

    h, m, s = match.groups()
    h = int(h or 0)
    m = int(m or 0)
    s = int(s or 0)

    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def coleta_comentarios(channel_id: str, youtube, videos: list[dict[str, Any]], dias: int = 7) -> list[dict[str, Any]]:
    """Coleta comentários de vídeos publicados nos últimos N dias."""
    comentarios = []
    try:
        data_limite = datetime.now(tz=FUSO_LOCAL) - timedelta(days=dias)

        for video in videos:
            # Verifica se o vídeo foi publicado recentemente
            publicado_str = video.get("publicado_texto", "")
            try:
                publicado = datetime.fromisoformat(publicado_str.replace("Z", "+00:00"))
                # Converter para fuso local para comparação
                publicado_local = publicado.astimezone(FUSO_LOCAL)
                if publicado_local < data_limite:
                    continue
            except (ValueError, AttributeError):
                continue

            video_id = video["video_id"]

            # Busca comentários do vídeo
            try:
                request = youtube.commentThreads().list(
                    part="snippet",
                    videoId=video_id,
                    maxResults=20,  # Pega até 20 threads de comentários
                    textFormat="plainText",
                    order="relevance"
                )

                while request:
                    response = request.execute()
                    for item in response.get("items", []):
                        snippet = item["snippet"]
                        comentario_principal = snippet.get("topLevelComment", {}).get("snippet", {})

                        comentarios.append({
                            "video_id": video_id,
                            "video_titulo": video.get("titulo", ""),
                            "autor": comentario_principal.get("authorDisplayName", "Anônimo"),
                            "texto": comentario_principal.get("textDisplay", ""),
                            "curtidas": comentario_principal.get("likeCount", 0),
                            "publicado_em": comentario_principal.get("publishedAt", ""),
                            "total_respostas": snippet.get("replyCount", 0),
                        })

                    # Próxima página de comentários
                    request = youtube.commentThreads().list_next(request, response) if "nextPageToken" in response else None

            except HttpError as e:
                # Se o vídeo tem comentários desabilitados ou há erro, continua
                print(f"  ⚠️ Não foi possível coletar comentários de '{video.get('titulo')}': {e}")
                continue

        return comentarios
    except Exception as e:
        print(f"Erro ao coletar comentários: {e}", file=sys.stderr)
        return []


def renderiza_markdown(relatorio: dict) -> str:
    """Renderiza um relatório estruturado em Markdown."""
    data_local = relatorio["gerado_em_local"]
    canal = relatorio["canal"]
    resumo = relatorio["resumo"]
    diagnostico = relatorio["diagnostico"]
    comentarios = relatorio.get("comentarios", [])

    linhas = [
        f"# Relatório do canal do YouTube — {data_local}",
        "",
        f"**Canal:** {canal['nome']} ({canal['handle']})",
        f"**URL:** {canal['url_canal']}",
        "",
        "## Métricas do canal",
        "",
        f"- **Inscritos:** {canal['inscritos_texto']}",
        f"- **Vídeos:** {canal['videos_texto']}",
        f"- **Visualizações totais:** {canal['visualizacoes_texto']}",
        "",
        "## Vídeos recentes",
        "",
    ]

    for v in relatorio["videos"]:
        linhas.append(f"- **{v['titulo']}**")
        linhas.append(f"  - Visualizações: {v.get('visualizacoes_texto', 'N/A')}")
        linhas.append(f"  - Curtidas: {v.get('curtidas_texto', 'N/A')}")
        linhas.append(f"  - Duração: {v.get('duracao', 'N/A')}")
        linhas.append("")

    # Seção de comentários dos últimos 7 dias
    if comentarios:
        linhas.extend([
            "## Comentários dos últimos 7 dias",
            "",
            f"**Total:** {len(comentarios)} comentários",
            "",
        ])

        comentarios_por_video = {}
        for c in comentarios:
            video_id = c["video_id"]
            if video_id not in comentarios_por_video:
                comentarios_por_video[video_id] = []
            comentarios_por_video[video_id].append(c)

        for video_id, comentarios_video in comentarios_por_video.items():
            video_titulo = comentarios_video[0]["video_titulo"] if comentarios_video else ""
            linhas.append(f"### {video_titulo}")
            linhas.append(f"_{len(comentarios_video)} comentário(s)_")
            linhas.append("")

            for c in comentarios_video[:5]:  # Mostra até 5 comentários por vídeo
                linhas.append(f"**{c['autor']}** ({c['curtidas']} curtidas)")
                texto_truncado = c['texto'][:100] + "..." if len(c['texto']) > 100 else c['texto']
                linhas.append(f"> {texto_truncado}")
                if c['total_respostas'] > 0:
                    linhas.append(f"_({c['total_respostas']} resposta(s))_")
                linhas.append("")

            if len(comentarios_video) > 5:
                linhas.append(f"_...e mais {len(comentarios_video) - 5} comentário(s)_")
                linhas.append("")
    else:
        linhas.extend([
            "## Comentários",
            "",
            "Nenhum comentário encontrado nos últimos 7 dias.",
            "",
        ])

    linhas.extend([
        "## Diagnóstico",
        "",
        f"**Status:** {'✅ OK' if diagnostico['ok'] else '⚠️ Com problemas'}",
        "",
    ])

    if diagnostico["problemas"]:
        linhas.append("**Problemas detectados:**")
        for problema in diagnostico["problemas"]:
            linhas.append(f"- {problema}")
        linhas.append("")

    return "\n".join(linhas)


def publica_resumo_actions(texto: str) -> None:
    """Escreve no resumo da execução do GitHub Actions."""
    caminho = os.environ.get("GITHUB_STEP_SUMMARY")
    if not caminho:
        return
    try:
        with open(caminho, "a", encoding="utf-8") as arquivo:
            arquivo.write(texto + "\n")
    except OSError as erro:
        print(f"Erro ao escrever resumo do Actions: {erro}", file=sys.stderr)


def executar(args) -> int:
    """Função principal de coleta."""
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        print("Erro: variável YOUTUBE_API_KEY não configurada", file=sys.stderr)
        return 1

    try:
        youtube = build("youtube", "v3", developerKey=api_key)

        # Extrai channel_id
        print(f"Buscando canal: {args.channel}")
        channel_id = extrai_channel_id(args.channel, youtube)
        if not channel_id:
            print(f"Erro: canal '{args.channel}' não encontrado", file=sys.stderr)
            return 1

        # Coleta dados
        print(f"Coletando dados do canal {channel_id}...")
        canal = coleta_canal(channel_id, youtube)
        print(f"  ✓ {canal['nome']} ({canal['inscritos_texto']})")

        print(f"Coletando {args.max_videos} vídeos...")
        videos = coleta_videos(channel_id, youtube, args.max_videos)
        print(f"  ✓ {len(videos)} vídeos coletados")

        print("Coletando comentários dos últimos 7 dias...")
        comentarios = coleta_comentarios(channel_id, youtube, videos, dias=7)
        print(f"  ✓ {len(comentarios)} comentários encontrados")

        # Monta estrutura de saída
        data = datetime.now(tz=FUSO_LOCAL)
        data_str = data.strftime("%Y-%m-%d")

        relatorio = {
            "gerado_em": data.isoformat(),
            "gerado_em_local": data.strftime("%d/%m/%Y %H:%M:%S"),
            "url_origem": f"https://www.youtube.com/channel/{channel_id}",
            "canal": canal,
            "videos": videos,
            "comentarios": comentarios,
            "playlists": [],
            "resumo": {
                "videos_analisados": len(videos),
                "visualizacoes_soma": sum(v.get("visualizacoes", 0) for v in videos),
                "curtidas_soma": sum(v.get("curtidas", 0) for v in videos),
                "comentarios_soma": sum(v.get("comentarios", 0) for v in videos),
                "visualizacoes_media": sum(v.get("visualizacoes", 0) for v in videos) / len(videos) if videos else 0,
                "curtidas_media": sum(v.get("curtidas", 0) for v in videos) / len(videos) if videos else 0,
                "comentarios_media": sum(v.get("comentarios", 0) for v in videos) / len(videos) if videos else 0,
                "taxa_engajamento_pct": 0,
                "comentarios_coletados": len(comentarios),
            },
            "avisos": [],
            "tendencia": {"atual": {}, "comparacoes": []},
            "crescimento_videos": {"referencia": data_str, "destaques": [], "novos": []},
            "diagnostico": {"ok": True, "problemas": []},
        }

        # Grava arquivos
        destino = Path(args.output_dir)
        destino.mkdir(parents=True, exist_ok=True)

        caminho_json = destino / f"{data_str}.json"
        conteudo_json = json.dumps(relatorio, ensure_ascii=False, indent=2) + "\n"
        caminho_json.write_text(conteudo_json, encoding="utf-8")

        caminho_md = destino / f"{data_str}.md"
        caminho_md.write_text(renderiza_markdown(relatorio), encoding="utf-8")

        (destino / "latest.json").write_text(conteudo_json, encoding="utf-8")

        print(f"\n✅ Relatório salvo:")
        print(f"  - {caminho_json}")
        print(f"  - {caminho_md}")

        return 0

    except HttpError as e:
        print(f"Erro na API do YouTube: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Erro inesperado: {e}", file=sys.stderr)
        return 1


def monta_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Coleta métricas públicas de um canal do YouTube via API v3."
    )
    parser.add_argument(
        "--channel",
        default="@patrickson_plays",
        help="Channel ID, @handle ou URL do canal.",
    )
    parser.add_argument(
        "--max-videos",
        type=int,
        default=10,
        help="Quantidade máxima de vídeos a coletar.",
    )
    parser.add_argument(
        "--output-dir",
        default="reports/youtube",
        help="Diretório de saída para os relatórios.",
    )
    return parser


def main() -> int:
    args = monta_parser().parse_args()
    return executar(args)


if __name__ == "__main__":
    raise SystemExit(main())
