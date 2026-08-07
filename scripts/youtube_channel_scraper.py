#!/usr/bin/env python3
"""Coletor de métricas públicas de um canal do YouTube usando Playwright.

O script abre o canal em um Chromium headless, lê o objeto `ytInitialData`
que o próprio YouTube injeta na página (bem mais estável do que depender de
seletores CSS) e complementa com leitura do DOM onde o dado só existe depois
que a página carrega dinamicamente — é o caso da contagem de comentários.

Saídas geradas no diretório de relatórios:
  - <data>.json           -> dados brutos estruturados
  - <data>.md             -> relatório legível, com tendência e diagnóstico
  - latest.json           -> cópia do último JSON gerado
  - historico.csv         -> série temporal das métricas do canal
  - historico_videos.csv  -> série temporal das métricas por vídeo

Códigos de saída: 0 sucesso, 1 falha na coleta, 2 relatório gravado mas com
problemas no diagnóstico (só com `--falhar-com-problemas`).

Uso:
    python scripts/youtube_channel_scraper.py \
        --channel https://youtube.com/@patrickson_plays \
        --max-videos 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright

import diagnostico
import historico

CANAL_PADRAO = "https://youtube.com/@patrickson_plays"
FUSO_LOCAL = timezone(timedelta(hours=-3))  # America/Fortaleza
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Sufixos abreviados que o YouTube usa em pt-BR e en-US ("1,2 mil", "1.2K").
MULTIPLICADORES = {
    "mil": 1_000,
    "mi": 1_000_000,
    "bi": 1_000_000_000,
    "k": 1_000,
    "m": 1_000_000,
    "b": 1_000_000_000,
}

RE_NUMERO = re.compile(r"(\d[\d.,\u00a0\u202f ]*)\s*(mil|mi|bi|k|m|b)?\b", re.IGNORECASE)
RE_INITIAL_DATA = re.compile(r"var ytInitialData\s*=\s*(\{.*?\});</script>", re.S)


# ---------------------------------------------------------------------------
# Utilidades de parsing (puras — testáveis sem navegador)
# ---------------------------------------------------------------------------


def parse_numero(texto: str | None) -> int | None:
    """Converte '120 inscritos', '39.008 visualizações' ou '1,2 mil' em inteiro."""
    if not texto:
        return None
    m = RE_NUMERO.search(texto)
    if not m:
        return None

    bruto, sufixo = m.group(1), (m.group(2) or "").lower()
    limpo = re.sub(r"[\s\u00a0\u202f]", "", bruto)
    if not limpo:
        return None

    if sufixo:
        # Com sufixo o número vem em forma decimal: "1,2 mil" (pt) ou "1.2K" (en).
        decimal = limpo.replace(".", "").replace(",", ".") if "," in limpo else limpo
        try:
            return int(round(float(decimal) * MULTIPLICADORES[sufixo]))
        except ValueError:
            return None

    # Sem sufixo, "." e "," são apenas separadores de milhar.
    digitos = re.sub(r"[^\d]", "", limpo)
    return int(digitos) if digitos else None


def texto_de(no: Any) -> str | None:
    """Extrai texto dos vários formatos que o YouTube usa em seus renderers."""
    if no is None:
        return None
    if isinstance(no, str):
        return no
    if isinstance(no, dict):
        if isinstance(no.get("content"), str):
            return no["content"]
        if isinstance(no.get("simpleText"), str):
            return no["simpleText"]
        if isinstance(no.get("runs"), list):
            return "".join(r.get("text", "") for r in no["runs"] if isinstance(r, dict))
        for chave in ("text", "label", "accessibilityText"):
            if chave in no:
                return texto_de(no[chave])
    return None


def busca_profunda(no: Any, chave: str) -> Iterator[Any]:
    """Percorre a árvore JSON e devolve todos os valores associados a `chave`."""
    if isinstance(no, dict):
        for k, v in no.items():
            if k == chave:
                yield v
            else:
                yield from busca_profunda(v, chave)
    elif isinstance(no, list):
        for item in no:
            yield from busca_profunda(item, chave)


def primeiro(iteravel: Iterator[Any], padrao: Any = None) -> Any:
    return next(iter(iteravel), padrao)


def maior_imagem(fontes: list[dict] | None) -> str | None:
    if not fontes:
        return None
    validas = [f for f in fontes if isinstance(f, dict) and f.get("url")]
    if not validas:
        return None
    return max(validas, key=lambda f: f.get("width", 0)).get("url")


def extrai_initial_data(html: str) -> dict | None:
    """Fallback: lê o ytInitialData direto do HTML quando o JS não expôs a variável."""
    m = RE_INITIAL_DATA.search(html or "")
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Parsers de cada página
# ---------------------------------------------------------------------------


def parse_cabecalho_canal(dados: dict) -> dict:
    """Lê nome, @handle, inscritos, nº de vídeos, avatar e banner do cabeçalho."""
    info: dict[str, Any] = {}
    header = primeiro(busca_profunda(dados, "pageHeaderViewModel"), {}) or {}

    info["nome"] = texto_de(header.get("title", {}).get("dynamicTextViewModel", {}).get("text"))
    if not info["nome"]:
        info["nome"] = texto_de(primeiro(busca_profunda(dados, "pageTitle")))

    linhas = (
        header.get("metadata", {})
        .get("contentMetadataViewModel", {})
        .get("metadataRows", [])
    )
    partes: list[str] = []
    for linha in linhas:
        for parte in linha.get("metadataParts", []):
            valor = texto_de(parte.get("text"))
            if valor:
                partes.append(valor)

    for valor in partes:
        baixo = valor.lower()
        if valor.startswith("@"):
            info["handle"] = valor
        elif "inscrit" in baixo or "subscrib" in baixo:
            info["inscritos_texto"] = valor
            info["inscritos"] = parse_numero(valor)
        elif "vídeo" in baixo or "video" in baixo:
            info["videos_texto"] = valor
            info["total_videos"] = parse_numero(valor)

    avatar = primeiro(busca_profunda(header.get("image", {}), "sources"))
    info["avatar_url"] = maior_imagem(avatar)
    banner = primeiro(busca_profunda(header.get("banner", {}), "sources"))
    info["banner_url"] = maior_imagem(banner)

    metadados = dados.get("metadata", {}).get("channelMetadataRenderer", {})
    if metadados:
        info.setdefault("nome", metadados.get("title"))
        info["descricao"] = metadados.get("description")
        info["channel_id"] = metadados.get("externalId")
        info["url_canal"] = metadados.get("vanityChannelUrl") or metadados.get("channelUrl")
        info["rss"] = metadados.get("rssUrl")
        info["palavras_chave"] = metadados.get("keywords")

    return {k: v for k, v in info.items() if v not in (None, "", [])}


def parse_sobre_canal(dados: dict) -> dict:
    """Lê a aba 'Sobre': descrição, visualizações totais, data de criação, links."""
    sobre = primeiro(busca_profunda(dados, "aboutChannelViewModel"))
    if not sobre:
        return {}

    info: dict[str, Any] = {
        "descricao": (sobre.get("description") or "").strip() or None,
        "pais": sobre.get("country"),
        "channel_id": sobre.get("channelId"),
        "url_canal": sobre.get("canonicalChannelUrl"),
        "inscritos_texto": sobre.get("subscriberCountText"),
        "inscritos": parse_numero(sobre.get("subscriberCountText")),
        "videos_texto": sobre.get("videoCountText"),
        "total_videos": parse_numero(sobre.get("videoCountText")),
        "visualizacoes_texto": sobre.get("viewCountText"),
        "visualizacoes_totais": parse_numero(sobre.get("viewCountText")),
        "criado_em_texto": texto_de(sobre.get("joinedDateText")),
    }

    links: list[dict[str, str]] = []
    for bruto in sobre.get("links", []) or []:
        link = bruto.get("channelExternalLinkViewModel", {})
        titulo = texto_de(link.get("title"))
        destino = texto_de(link.get("link"))
        if titulo or destino:
            links.append({"titulo": titulo, "url": destino})
    if links:
        info["links"] = links

    return {k: v for k, v in info.items() if v not in (None, "", [])}


def parse_aba_videos(dados: dict, limite: int) -> list[dict]:
    """Lê a aba de vídeos (formato `lockupViewModel`) e devolve a lista ordenada."""
    videos: list[dict] = []
    vistos: set[str] = set()

    for lockup in busca_profunda(dados, "lockupViewModel"):
        if len(videos) >= limite:
            break
        if not isinstance(lockup, dict):
            continue
        if lockup.get("contentType") not in (None, "LOCKUP_CONTENT_TYPE_VIDEO"):
            continue
        video_id = lockup.get("contentId")
        if not video_id or video_id in vistos:
            continue
        vistos.add(video_id)

        meta = lockup.get("metadata", {}).get("lockupMetadataViewModel", {})
        video: dict[str, Any] = {
            "video_id": video_id,
            "titulo": texto_de(meta.get("title")),
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "thumbnail": maior_imagem(primeiro(busca_profunda(lockup.get("contentImage", {}), "sources"))),
        }

        linhas = (
            meta.get("metadata", {})
            .get("contentMetadataViewModel", {})
            .get("metadataRows", [])
        )
        for linha in linhas:
            for parte in linha.get("metadataParts", []):
                rotulo = parte.get("accessibilityLabel") or texto_de(parte.get("text"))
                if not rotulo:
                    continue
                baixo = rotulo.lower()
                if "visualiz" in baixo or "view" in baixo:
                    video["visualizacoes_texto"] = rotulo
                    video["visualizacoes"] = parse_numero(rotulo)
                elif "há " in baixo or " ago" in baixo:
                    video["publicado_texto"] = rotulo

        duracao = primeiro(busca_profunda(lockup.get("contentImage", {}), "thumbnailBadgeViewModel"))
        if isinstance(duracao, dict) and duracao.get("text"):
            video["duracao"] = duracao["text"]

        videos.append(video)
        if len(videos) >= limite:
            break

    return videos


def parse_pagina_video(dados: dict, player: dict | None) -> dict:
    """Extrai curtidas, visualizações e metadados da página de um vídeo."""
    info: dict[str, Any] = {}

    for factoid in busca_profunda(dados, "factoidRenderer"):
        rotulo = (texto_de(factoid.get("label")) or "").lower()
        valor = texto_de(factoid.get("accessibilityText")) or texto_de(factoid.get("value"))
        if "gostei" in rotulo or "like" in rotulo:
            info["curtidas_texto"] = texto_de(factoid.get("value"))
            info["curtidas"] = parse_numero(valor)

    if "curtidas" not in info:
        # Fallback: título do botão de like (que também traz o total).
        segmentado = primeiro(busca_profunda(dados, "segmentedLikeDislikeButtonViewModel"), {}) or {}
        for botao in busca_profunda(segmentado, "buttonViewModel"):
            if isinstance(botao, dict) and botao.get("iconName") == "LIKE":
                info["curtidas_texto"] = botao.get("title")
                info["curtidas"] = parse_numero(botao.get("title"))
                break

    contador = primeiro(busca_profunda(dados, "videoViewCountRenderer"), {}) or {}
    if contador:
        info["visualizacoes_texto"] = texto_de(contador.get("viewCount"))
        info["visualizacoes"] = parse_numero(info["visualizacoes_texto"])

    if player:
        detalhes = player.get("videoDetails", {}) or {}
        micro = player.get("microformat", {}).get("playerMicroformatRenderer", {}) or {}
        if detalhes.get("viewCount"):
            info["visualizacoes"] = parse_numero(detalhes["viewCount"])
        info["titulo"] = detalhes.get("title") or info.get("titulo")
        info["duracao_segundos"] = parse_numero(detalhes.get("lengthSeconds"))
        info["descricao"] = (detalhes.get("shortDescription") or "").strip() or None
        info["tags"] = detalhes.get("keywords")
        info["publicado_em"] = micro.get("publishDate")
        info["categoria"] = micro.get("category")

    return {k: v for k, v in info.items() if v not in (None, "", [])}


# ---------------------------------------------------------------------------
# Coleta com o navegador
# ---------------------------------------------------------------------------


class ColetorYouTube:
    def __init__(self, contexto, idioma: str, timeout: int, avisos: list[str]):
        self.contexto = contexto
        self.idioma = idioma
        self.timeout = timeout
        self.avisos = avisos

    def _com_idioma(self, url: str) -> str:
        separador = "&" if "?" in url else "?"
        return f"{url}{separador}hl={self.idioma}&persist_hl=1&gl=BR"

    async def _aceitar_consentimento(self, pagina) -> None:
        """O YouTube pode interceptar a navegação com a tela de consentimento."""
        try:
            if "consent." not in pagina.url:
                seletor = "ytd-consent-bump-v2-lightbox"
                if await pagina.locator(seletor).count() == 0:
                    return
            botao = pagina.get_by_role(
                "button", name=re.compile(r"aceitar tudo|accept all|rejeitar tudo|reject all", re.I)
            )
            if await botao.count():
                await botao.first.click(timeout=10_000)
                await pagina.wait_for_load_state("domcontentloaded", timeout=self.timeout)
        except (PlaywrightError, PlaywrightTimeout) as erro:
            self.avisos.append(f"Falha ao tratar a tela de consentimento: {erro}")

    async def abrir(self, pagina, url: str) -> tuple[dict | None, dict | None]:
        """Navega e devolve (ytInitialData, ytInitialPlayerResponse)."""
        await pagina.goto(self._com_idioma(url), wait_until="domcontentloaded", timeout=self.timeout)
        await self._aceitar_consentimento(pagina)
        try:
            await pagina.wait_for_function("() => !!window.ytInitialData", timeout=self.timeout)
        except PlaywrightTimeout:
            self.avisos.append(f"ytInitialData não apareceu via JS em {url}; usando o HTML.")

        dados = await pagina.evaluate("() => window.ytInitialData ?? null")
        if not dados:
            dados = extrai_initial_data(await pagina.content())
        player = await pagina.evaluate("() => window.ytInitialPlayerResponse ?? null")
        return dados, player

    async def coleta_canal(self, url_canal: str) -> dict:
        pagina = await self.contexto.new_page()
        try:
            dados, _ = await self.abrir(pagina, url_canal)
            canal = parse_cabecalho_canal(dados or {})

            dados_sobre, _ = await self.abrir(pagina, f"{url_canal.rstrip('/')}/about")
            sobre = parse_sobre_canal(dados_sobre or {})
            if not sobre:
                self.avisos.append("Aba 'Sobre' não retornou dados; usando apenas o cabeçalho.")
            canal.update(sobre)
            return canal
        finally:
            await pagina.close()

    async def coleta_videos(self, url_canal: str, limite: int) -> list[dict]:
        pagina = await self.contexto.new_page()
        try:
            dados, _ = await self.abrir(pagina, f"{url_canal.rstrip('/')}/videos")
            videos = parse_aba_videos(dados or {}, limite)

            # A aba entrega ~30 itens; rola a página para carregar mais se preciso.
            tentativas = 0
            while len(videos) < limite and tentativas < 10:
                anterior = len(videos)
                await pagina.mouse.wheel(0, 4000)
                await pagina.wait_for_timeout(1500)
                atual = await pagina.evaluate("() => window.ytInitialData ?? null")
                videos = parse_aba_videos(atual or dados or {}, limite)
                # O conteúdo paginado entra no DOM, não no ytInitialData: complementa.
                videos = self._mescla_dom(videos, await self._videos_do_dom(pagina, limite), limite)
                if len(videos) == anterior:
                    tentativas += 1
            return videos[:limite]
        finally:
            await pagina.close()

    async def _videos_do_dom(self, pagina, limite: int) -> list[dict]:
        """Lê os cards já renderizados — cobre os itens carregados por scroll."""
        try:
            return await pagina.evaluate(
                """(limite) => {
                    const saida = [];
                    for (const card of document.querySelectorAll('ytd-rich-item-renderer')) {
                        const link = card.querySelector('a[href*="/watch?v="]');
                        if (!link) continue;
                        const id = new URL(link.href).searchParams.get('v');
                        if (!id) continue;
                        const titulo = card.querySelector('#video-title, h3 a span')?.textContent?.trim();
                        const metas = [...card.querySelectorAll('.inline-metadata-item, #metadata-line span')]
                            .map((n) => n.textContent.trim()).filter(Boolean);
                        saida.push({ video_id: id, titulo, metas });
                        if (saida.length >= limite) break;
                    }
                    return saida;
                }""",
                limite,
            )
        except PlaywrightError as erro:
            self.avisos.append(f"Não foi possível ler os cards de vídeo do DOM: {erro}")
            return []

    def _mescla_dom(self, videos: list[dict], do_dom: list[dict], limite: int) -> list[dict]:
        conhecidos = {v["video_id"] for v in videos}
        for bruto in do_dom:
            if bruto["video_id"] in conhecidos or len(videos) >= limite:
                continue
            video = {
                "video_id": bruto["video_id"],
                "titulo": bruto.get("titulo"),
                "url": f"https://www.youtube.com/watch?v={bruto['video_id']}",
            }
            for meta in bruto.get("metas", []):
                baixo = meta.lower()
                if "visualiz" in baixo or "view" in baixo:
                    video["visualizacoes_texto"] = meta
                    video["visualizacoes"] = parse_numero(meta)
                elif "há " in baixo or " ago" in baixo:
                    video["publicado_texto"] = meta
            videos.append(video)
            conhecidos.add(bruto["video_id"])
        return videos

    async def coleta_detalhes_video(self, video: dict, com_comentarios: bool) -> dict:
        pagina = await self.contexto.new_page()
        try:
            dados, player = await self.abrir(pagina, video["url"])
            detalhes = parse_pagina_video(dados or {}, player)
            if com_comentarios:
                detalhes.update(await self._contagem_comentarios(pagina, video["video_id"]))
            return {**video, **detalhes}
        except (PlaywrightError, PlaywrightTimeout) as erro:
            self.avisos.append(f"Falha ao coletar o vídeo {video['video_id']}: {erro}")
            return {**video, "erro": str(erro).splitlines()[0]}
        finally:
            await pagina.close()

    # O cabeçalho dos comentários é renderizado antes da contagem chegar, e nesse
    # meio-tempo mostra só a palavra "Comentários". Por isso o texto só é aceito
    # quando realmente contém um número.
    SELETORES_CONTAGEM = (
        "ytd-comments-header-renderer #count",
        "ytd-comments-header-renderer #title",
        "ytd-comments #count",
    )
    SELETORES_SEM_COMENTARIOS = (
        "ytd-item-section-renderer[section-identifier='comment-item-section'] ytd-message-renderer",
        "ytd-comments ytd-message-renderer",
    )

    async def _contagem_comentarios(self, pagina, video_id: str) -> dict:
        """Os comentários só chegam depois do scroll — daí a necessidade do navegador."""
        try:
            for tentativa in range(12):
                await pagina.mouse.wheel(0, 1500)
                await pagina.wait_for_timeout(800)

                for seletor in self.SELETORES_CONTAGEM:
                    alvo = pagina.locator(seletor)
                    if not await alvo.count():
                        continue
                    texto = (await alvo.first.inner_text()).strip()
                    quantidade = parse_numero(texto)
                    if quantidade is not None:
                        return {"comentarios_texto": texto, "comentarios": quantidade}

                # Só depois de dar tempo à contagem: se há mensagem no lugar da lista,
                # é vídeo sem comentários ou com comentários desativados.
                if tentativa >= 3:
                    for seletor in self.SELETORES_SEM_COMENTARIOS:
                        alvo = pagina.locator(seletor)
                        if not await alvo.count():
                            continue
                        texto = (await alvo.first.inner_text()).strip()
                        if texto:
                            return {"comentarios_texto": texto, "comentarios": 0}

            self.avisos.append(
                f"Contagem de comentários não carregou a tempo no vídeo {video_id}."
            )
        except (PlaywrightError, PlaywrightTimeout) as erro:
            self.avisos.append(f"Falha ao ler os comentários do vídeo {video_id}: {erro}")
        return {}


# ---------------------------------------------------------------------------
# Montagem do relatório
# ---------------------------------------------------------------------------


def monta_resumo(videos: list[dict]) -> dict:
    analisados = [v for v in videos if "erro" not in v]
    soma = lambda chave: sum(v.get(chave) or 0 for v in analisados)  # noqa: E731
    total_views = soma("visualizacoes")
    total_likes = soma("curtidas")
    total_comentarios = soma("comentarios")
    qtd = len(analisados) or 1

    mais_visto = max(analisados, key=lambda v: v.get("visualizacoes") or 0, default=None)
    mais_curtido = max(analisados, key=lambda v: v.get("curtidas") or 0, default=None)

    return {
        "videos_analisados": len(analisados),
        "visualizacoes_soma": total_views,
        "curtidas_soma": total_likes,
        "comentarios_soma": total_comentarios,
        "visualizacoes_media": round(total_views / qtd, 1),
        "curtidas_media": round(total_likes / qtd, 1),
        "comentarios_media": round(total_comentarios / qtd, 1),
        "taxa_engajamento_pct": (
            round((total_likes + total_comentarios) / total_views * 100, 2)
            if total_views
            else None
        ),
        "video_mais_visto": (
            {"titulo": mais_visto.get("titulo"), "url": mais_visto.get("url"),
             "visualizacoes": mais_visto.get("visualizacoes")}
            if mais_visto else None
        ),
        "video_mais_curtido": (
            {"titulo": mais_curtido.get("titulo"), "url": mais_curtido.get("url"),
             "curtidas": mais_curtido.get("curtidas")}
            if mais_curtido else None
        ),
    }


def formata(valor: Any) -> str:
    if valor is None:
        return "—"
    if isinstance(valor, int):
        return f"{valor:,}".replace(",", ".")
    if isinstance(valor, float):
        return f"{valor:,.1f}".replace(",", "@").replace(".", ",").replace("@", ".")
    return str(valor)


def formata_delta(valor: Any) -> str:
    """Variação com sinal explícito: '+153', '-4', 'estável'."""
    if valor is None:
        return "—"
    if valor == 0:
        return "estável"
    return f"+{formata(valor)}" if valor > 0 else f"-{formata(abs(valor))}"


def formata_data(data: str | None) -> str:
    """'2026-08-06' vira '06/08' — o ano só polui a tabela de tendência."""
    if not data:
        return "—"
    partes = data.split("-")
    return f"{partes[2]}/{partes[1]}" if len(partes) == 3 else data


def plural(quantidade: Any, singular: str, plural_: str) -> str:
    return singular if quantidade == 1 else plural_


def renderiza_tendencia(tendencia: dict, crescimento: dict) -> list[str]:
    """Seção de evolução: sem histórico suficiente, explica em vez de sumir."""
    comparacoes = tendencia.get("comparacoes") or []
    if not comparacoes:
        return [
            "## Tendência",
            "",
            "_Ainda não há coleta anterior para comparar. A partir da próxima "
            "execução esta seção mostra a evolução do canal._",
            "",
        ]

    atual = tendencia.get("atual") or {}
    rotulos = {
        "inscritos": "Inscritos",
        "total_videos": "Vídeos publicados",
        "visualizacoes_totais": "Visualizações totais",
    }

    cabecalho = "| Métrica | Atual |" + "".join(
        f" vs. {formata_data(c['data'])} ({c['dias']}d) |" for c in comparacoes
    )
    linhas = [
        "## Tendência",
        "",
        cabecalho,
        "|---|---:|" + "---:|" * len(comparacoes),
    ]
    for campo, rotulo in rotulos.items():
        celulas = "".join(
            f" {formata_delta((c.get('deltas') or {}).get(campo))} |" for c in comparacoes
        )
        linhas.append(f"| {rotulo} | {formata(atual.get(campo))} |{celulas}")

    # Ritmo médio de visualizações na janela mais longa disponível.
    mais_longa = max(comparacoes, key=lambda c: c.get("dias") or 0)
    ganho = (mais_longa.get("deltas") or {}).get("visualizacoes_totais")
    dias = mais_longa.get("dias") or 0
    if ganho is not None and dias > 0:
        linhas += [
            "",
            f"📈 Média de **{formata(round(ganho / dias, 1))}** "
            f"{plural(ganho, 'visualização', 'visualizações')} por dia nos últimos "
            f"{dias} {plural(dias, 'dia', 'dias')}.",
        ]

    linhas.append("")

    destaques = crescimento.get("destaques") or []
    if destaques:
        linhas += [
            f"### Vídeos que mais cresceram desde {formata_data(crescimento.get('referencia'))}",
            "",
            "| Vídeo | Visualizações | Δ views | Δ curtidas |",
            "|---|---:|---:|---:|",
        ]
        for video in destaques:
            titulo = (video.get("titulo") or video.get("video_id") or "—").replace("|", "\\|")
            url = f"https://www.youtube.com/watch?v={video.get('video_id')}"
            linhas.append(
                f"| [{titulo}]({url}) | {formata(video.get('visualizacoes'))} | "
                f"{formata_delta(video.get('delta_visualizacoes'))} | "
                f"{formata_delta(video.get('delta_curtidas'))} |"
            )
        linhas.append("")

    novos = crescimento.get("novos") or []
    if novos:
        linhas += [
            f"🆕 **{len(novos)} {plural(len(novos), 'vídeo novo', 'vídeos novos')} "
            "na amostra desde a coleta anterior:**",
            "",
        ]
        linhas += [
            f"- [{(v.get('titulo') or v.get('video_id')).replace('|', chr(92) + '|')}]"
            f"(https://www.youtube.com/watch?v={v.get('video_id')}) — "
            f"{formata(v.get('visualizacoes'))} {plural(v.get('visualizacoes'), 'visualização', 'visualizações')}"
            for v in novos
        ]
        linhas.append("")

    return linhas


def renderiza_markdown(relatorio: dict) -> str:
    canal = relatorio["canal"]
    resumo = relatorio["resumo"]
    videos = relatorio["videos"]

    linhas = [
        f"# Relatório do canal — {canal.get('nome') or 'Canal do YouTube'}",
        "",
        f"**Coletado em:** {relatorio['gerado_em_local']} (America/Fortaleza)  ",
        f"**Canal:** {canal.get('handle') or ''} — <{canal.get('url_canal') or relatorio['url_origem']}>  ",
        f"**Channel ID:** `{canal.get('channel_id') or '—'}`",
        "",
        "## Visão geral do canal",
        "",
        "| Métrica | Valor |",
        "|---|---|",
        f"| Inscritos | {formata(canal.get('inscritos'))} |",
        f"| Vídeos publicados | {formata(canal.get('total_videos'))} |",
        f"| Visualizações totais | {formata(canal.get('visualizacoes_totais'))} |",
        f"| País | {canal.get('pais') or '—'} |",
        f"| Canal criado em | {canal.get('criado_em_texto') or '—'} |",
        "",
        "### Descrição",
        "",
    ]

    descricao = (canal.get("descricao") or "").strip()
    linhas.extend([f"> {l}" if l.strip() else ">" for l in descricao.splitlines()] if descricao
                  else ["_Sem descrição pública._"])
    linhas.append("")

    if canal.get("links"):
        linhas += ["### Links do canal", ""]
        linhas += [f"- **{l['titulo'] or 'Link'}** — {l['url'] or '—'}" for l in canal["links"]]
        linhas.append("")

    linhas += renderiza_tendencia(
        relatorio.get("tendencia") or {}, relatorio.get("crescimento_videos") or {}
    )

    linhas += [
        f"## Últimos {len(videos)} vídeos",
        "",
        "| # | Vídeo | Visualizações | Curtidas | Comentários | Publicado |",
        "|---:|---|---:|---:|---:|---|",
    ]
    for i, v in enumerate(videos, 1):
        titulo = (v.get("titulo") or v["video_id"]).replace("|", "\\|")
        linhas.append(
            f"| {i} | [{titulo}]({v['url']}) | {formata(v.get('visualizacoes'))} | "
            f"{formata(v.get('curtidas'))} | {formata(v.get('comentarios'))} | "
            f"{v.get('publicado_texto') or v.get('publicado_em') or '—'} |"
        )

    linhas += [
        "",
        "## Resumo da amostra analisada",
        "",
        "| Métrica | Valor |",
        "|---|---|",
        f"| Vídeos analisados | {formata(resumo['videos_analisados'])} |",
        f"| Visualizações (soma) | {formata(resumo['visualizacoes_soma'])} |",
        f"| Curtidas (soma) | {formata(resumo['curtidas_soma'])} |",
        f"| Comentários (soma) | {formata(resumo['comentarios_soma'])} |",
        f"| Visualizações (média) | {formata(resumo['visualizacoes_media'])} |",
        f"| Curtidas (média) | {formata(resumo['curtidas_media'])} |",
        f"| Comentários (média) | {formata(resumo['comentarios_media'])} |",
        f"| Taxa de engajamento | {formata(resumo['taxa_engajamento_pct'])}% |",
        "",
    ]

    if resumo.get("video_mais_visto"):
        mv = resumo["video_mais_visto"]
        linhas.append(
            f"🏆 **Mais visto:** [{mv['titulo']}]({mv['url']}) — "
            f"{formata(mv['visualizacoes'])} {plural(mv['visualizacoes'], 'visualização', 'visualizações')}"
        )
    if resumo.get("video_mais_curtido"):
        mc = resumo["video_mais_curtido"]
        linhas.append(
            f"❤️ **Mais curtido:** [{mc['titulo']}]({mc['url']}) — "
            f"{formata(mc['curtidas'])} {plural(mc['curtidas'], 'curtida', 'curtidas')}"
        )

    if relatorio.get("avisos"):
        linhas += ["", "## Avisos da coleta", ""]
        linhas += [f"- {a}" for a in relatorio["avisos"]]

    if relatorio.get("diagnostico"):
        linhas += ["", diagnostico.resumo_texto(relatorio["diagnostico"])]

    linhas += [
        "",
        "---",
        "",
        "_Relatório gerado automaticamente por `scripts/youtube_channel_scraper.py` "
        "(Playwright + GitHub Actions). Os números refletem apenas dados públicos "
        "visíveis na página do canal._",
        "",
    ]
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------


def normaliza_url_canal(url: str) -> str:
    url = url.strip().split("?")[0].rstrip("/")
    if not url.startswith("http"):
        url = f"https://www.youtube.com/{url.lstrip('/')}"
    return url.replace("://youtube.com", "://www.youtube.com")


async def executar(args: argparse.Namespace) -> int:
    avisos: list[str] = []
    url_canal = normaliza_url_canal(args.channel)
    agora = datetime.now(timezone.utc)

    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    args_navegador = ["--no-sandbox", "--disable-dev-shm-usage", "--mute-audio"]

    async with async_playwright() as p:
        parametros: dict[str, Any] = {"headless": not args.headful, "args": args_navegador}
        if os.environ.get("CHROMIUM_EXECUTABLE_PATH"):
            parametros["executable_path"] = os.environ["CHROMIUM_EXECUTABLE_PATH"]
        if proxy:
            # `bypass` evita mandar tráfego local para o proxy (útil em dev/CI com proxy).
            sem_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
            parametros["proxy"] = {
                "server": proxy,
                "bypass": ",".join(filter(None, ["localhost", "127.0.0.1", "::1", sem_proxy])),
            }

        navegador = await p.chromium.launch(**parametros)
        contexto = await navegador.new_context(
            locale=args.lang,
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            extra_http_headers={"Accept-Language": f"{args.lang},pt;q=0.9,en;q=0.8"},
        )
        contexto.set_default_timeout(args.timeout)

        if not args.com_imagens:
            # Imagens, fontes e vídeo não são usados na coleta — abortá-los deixa
            # a execução no CI bem mais rápida e leve.
            async def bloqueia_recursos(rota):
                if rota.request.resource_type in {"image", "media", "font"}:
                    await rota.abort()
                else:
                    await rota.continue_()

            await contexto.route("**/*", bloqueia_recursos)

        try:
            coletor = ColetorYouTube(contexto, args.lang.split("-")[0], args.timeout, avisos)
            canal = await coletor.coleta_canal(url_canal)
            if not canal:
                print("ERRO: não foi possível ler os dados do canal.", file=sys.stderr)
                return 1

            videos = await coletor.coleta_videos(url_canal, args.max_videos)
            detalhados: list[dict] = []
            for i, video in enumerate(videos, 1):
                print(f"[{i}/{len(videos)}] {video.get('titulo') or video['video_id']}", flush=True)
                detalhados.append(
                    await coletor.coleta_detalhes_video(video, com_comentarios=not args.sem_comentarios)
                )
        finally:
            await navegador.close()

    relatorio = {
        "gerado_em": agora.isoformat(),
        "gerado_em_local": agora.astimezone(FUSO_LOCAL).strftime("%d/%m/%Y %H:%M:%S"),
        "url_origem": url_canal,
        "canal": canal,
        "videos": detalhados,
        "resumo": monta_resumo(detalhados),
        "avisos": avisos,
    }

    destino = Path(args.output_dir)
    destino.mkdir(parents=True, exist_ok=True)
    data = agora.astimezone(FUSO_LOCAL).strftime("%Y-%m-%d")

    # A série histórica é atualizada antes da tendência: reexecutar a coleta no
    # mesmo dia substitui a linha do dia em vez de duplicá-la.
    caminho_hist = destino / historico.ARQUIVO_CANAL
    caminho_hist_videos = destino / historico.ARQUIVO_VIDEOS
    serie = historico.upsert(
        historico.le_csv(caminho_hist), [historico.linha_do_canal(relatorio, data)], ("data",)
    )
    serie_videos = historico.upsert(
        historico.le_csv(caminho_hist_videos),
        historico.linhas_dos_videos(relatorio, data),
        ("data", "video_id"),
    )
    historico.grava_csv(caminho_hist, historico.COLUNAS_CANAL, serie)
    historico.grava_csv(caminho_hist_videos, historico.COLUNAS_VIDEOS, serie_videos)

    relatorio["tendencia"] = historico.monta_tendencia(serie, data)
    relatorio["crescimento_videos"] = historico.crescimento_videos(serie_videos, data)
    relatorio["diagnostico"] = diagnostico.avalia_saude(relatorio, relatorio["tendencia"])

    caminho_json = destino / f"{data}.json"
    caminho_md = destino / f"{data}.md"
    conteudo_json = json.dumps(relatorio, ensure_ascii=False, indent=2) + "\n"
    caminho_json.write_text(conteudo_json, encoding="utf-8")
    caminho_md.write_text(renderiza_markdown(relatorio), encoding="utf-8")
    (destino / "latest.json").write_text(conteudo_json, encoding="utf-8")

    print(f"\nRelatório gravado em:\n  {caminho_md}\n  {caminho_json}")
    print(f"Histórico atualizado: {caminho_hist} ({len(serie)} coletas)")
    if avisos:
        print("\nAvisos:")
        for aviso in avisos:
            print(f"  - {aviso}")

    saude = relatorio["diagnostico"]
    publica_resumo_actions(diagnostico.resumo_texto(saude))
    if not saude["ok"]:
        print("\nProblemas detectados:", file=sys.stderr)
        for problema in saude["problemas"]:
            print(f"  - {problema}", file=sys.stderr)
        if args.falhar_com_problemas:
            # Código próprio: o relatório foi gravado, mas os dados não são confiáveis.
            return 2

    return 0


def publica_resumo_actions(texto: str) -> None:
    """Escreve no resumo da execução do GitHub Actions, quando rodando lá."""
    caminho = os.environ.get("GITHUB_STEP_SUMMARY")
    if not caminho:
        return
    try:
        with open(caminho, "a", encoding="utf-8") as arquivo:
            arquivo.write(texto + "\n")
    except OSError as erro:
        print(f"Não foi possível escrever o resumo do Actions: {erro}", file=sys.stderr)


def monta_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Coleta métricas públicas de um canal do YouTube.")
    parser.add_argument("--channel", default=os.environ.get("YT_CHANNEL_URL", CANAL_PADRAO),
                        help="URL ou @handle do canal.")
    parser.add_argument("--max-videos", type=int,
                        default=int(os.environ.get("YT_MAX_VIDEOS", "10")),
                        help="Quantidade de vídeos recentes a detalhar (padrão: 10).")
    parser.add_argument("--output-dir", default=os.environ.get("YT_OUTPUT_DIR", "reports/youtube"),
                        help="Diretório onde o relatório será gravado.")
    parser.add_argument("--lang", default="pt-BR", help="Idioma da interface do YouTube.")
    parser.add_argument("--timeout", type=int, default=60_000, help="Timeout em milissegundos.")
    parser.add_argument("--sem-comentarios", action="store_true",
                        help="Pula a contagem de comentários (coleta bem mais rápida).")
    parser.add_argument("--falhar-com-problemas", action="store_true",
                        help="Encerra com código 2 se o diagnóstico apontar problemas.")
    parser.add_argument("--com-imagens", action="store_true",
                        help="Carrega imagens, fontes e mídia (por padrão são bloqueadas).")
    parser.add_argument("--headful", action="store_true", help="Abre o navegador com interface.")
    return parser


def main() -> int:
    args = monta_parser().parse_args()
    try:
        return asyncio.run(executar(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
