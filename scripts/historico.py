#!/usr/bin/env python3
"""Histórico das coletas: série temporal em CSV e cálculo de tendências.

O coletor grava um relatório por dia, mas um relatório isolado não mostra
evolução. Aqui cada coleta vira uma linha em dois CSVs acumulativos — um com
as métricas do canal, outro com as métricas por vídeo — e a comparação entre
datas vira a seção de tendência do relatório.

As funções são puras (recebem e devolvem listas de dicionários); só `le_csv`
e `grava_csv` tocam o disco.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

ARQUIVO_CANAL = "historico.csv"
ARQUIVO_VIDEOS = "historico_videos.csv"

COLUNAS_CANAL = (
    "data",
    "gerado_em",
    "inscritos",
    "total_videos",
    "visualizacoes_totais",
    "videos_analisados",
    "visualizacoes_soma",
    "curtidas_soma",
    "comentarios_soma",
    "taxa_engajamento_pct",
)

COLUNAS_VIDEOS = (
    "data",
    "video_id",
    "titulo",
    "visualizacoes",
    "curtidas",
    "comentarios",
)

# Métricas acumulativas do canal que fazem sentido comparar entre datas.
CAMPOS_TENDENCIA = ("inscritos", "total_videos", "visualizacoes_totais")

# Janelas de comparação: (rótulo, dias para trás).
JANELAS = (("1 dia", 1), ("7 dias", 7), ("30 dias", 30))


# ---------------------------------------------------------------------------
# Conversões
# ---------------------------------------------------------------------------


def numero(valor: Any) -> int | float | None:
    """Converte o texto vindo do CSV de volta para número (vazio vira None)."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return valor
    texto = str(valor).strip()
    if not texto:
        return None
    try:
        return int(texto)
    except ValueError:
        pass
    try:
        return float(texto)
    except ValueError:
        return None


def _texto(valor: Any) -> str:
    """Serializa para o CSV: None vira string vazia, o resto vira texto."""
    return "" if valor is None else str(valor)


def _data(valor: Any) -> date | None:
    try:
        return datetime.strptime(str(valor).strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Leitura e escrita
# ---------------------------------------------------------------------------


def le_csv(caminho: Path) -> list[dict[str, str]]:
    """Lê um CSV do histórico. Arquivo inexistente é simplesmente série vazia."""
    if not caminho.exists():
        return []
    with caminho.open(encoding="utf-8", newline="") as arquivo:
        return [dict(linha) for linha in csv.DictReader(arquivo)]


def grava_csv(caminho: Path, colunas: Sequence[str], linhas: Iterable[dict]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8", newline="") as arquivo:
        # `lineterminator` explícito evita CRLF, que sujaria o diff no Git.
        escritor = csv.DictWriter(
            arquivo, fieldnames=list(colunas), lineterminator="\n", extrasaction="ignore"
        )
        escritor.writeheader()
        for linha in linhas:
            escritor.writerow({coluna: _texto(linha.get(coluna)) for coluna in colunas})


# ---------------------------------------------------------------------------
# Montagem das linhas
# ---------------------------------------------------------------------------


def linha_do_canal(relatorio: dict, data: str) -> dict[str, Any]:
    canal = relatorio.get("canal") or {}
    resumo = relatorio.get("resumo") or {}
    return {
        "data": data,
        "gerado_em": relatorio.get("gerado_em"),
        "inscritos": canal.get("inscritos"),
        "total_videos": canal.get("total_videos"),
        "visualizacoes_totais": canal.get("visualizacoes_totais"),
        "videos_analisados": resumo.get("videos_analisados"),
        "visualizacoes_soma": resumo.get("visualizacoes_soma"),
        "curtidas_soma": resumo.get("curtidas_soma"),
        "comentarios_soma": resumo.get("comentarios_soma"),
        "taxa_engajamento_pct": resumo.get("taxa_engajamento_pct"),
    }


def linhas_dos_videos(relatorio: dict, data: str) -> list[dict[str, Any]]:
    linhas = []
    for video in relatorio.get("videos") or []:
        if "erro" in video or not video.get("video_id"):
            continue
        linhas.append({
            "data": data,
            "video_id": video["video_id"],
            "titulo": video.get("titulo"),
            "visualizacoes": video.get("visualizacoes"),
            "curtidas": video.get("curtidas"),
            "comentarios": video.get("comentarios"),
        })
    return linhas


def upsert(existentes: list[dict], novas: list[dict], chaves: Sequence[str]) -> list[dict]:
    """Insere as linhas novas substituindo as de mesma chave.

    Reexecutar a coleta no mesmo dia atualiza a linha do dia em vez de duplicá-la.
    """
    def identidade(linha: dict) -> tuple:
        return tuple(_texto(linha.get(chave)) for chave in chaves)

    substituidas = {identidade(linha) for linha in novas}
    mantidas = [linha for linha in existentes if identidade(linha) not in substituidas]
    resultado = mantidas + [dict(linha) for linha in novas]
    # A ordenação por data mantém o CSV legível e o diff do Git pequeno.
    resultado.sort(key=lambda linha: (_texto(linha.get("data")), _texto(linha.get("video_id"))))
    return resultado


# ---------------------------------------------------------------------------
# Tendências
# ---------------------------------------------------------------------------


def _linhas_por_data(historico: list[dict]) -> list[tuple[date, dict]]:
    pares = [(_data(linha.get("data")), linha) for linha in historico]
    return sorted([(d, linha) for d, linha in pares if d], key=lambda par: par[0])


def busca_referencia(historico: list[dict], atual: date, dias: int) -> dict | None:
    """Linha mais recente com data <= (atual - dias); None se não houver."""
    alvo = atual - timedelta(days=dias)
    candidatas = [linha for d, linha in _linhas_por_data(historico) if d <= alvo]
    return candidatas[-1] if candidatas else None


def calcula_deltas(atual: dict, anterior: dict, campos: Sequence[str]) -> dict[str, Any]:
    deltas: dict[str, Any] = {}
    for campo in campos:
        agora, antes = numero(atual.get(campo)), numero(anterior.get(campo))
        deltas[campo] = None if agora is None or antes is None else agora - antes
    return deltas


def monta_tendencia(historico: list[dict], data_atual: str) -> dict:
    """Compara a coleta de hoje com as janelas de 1, 7 e 30 dias."""
    hoje = _data(data_atual)
    linha_atual = next(
        (linha for linha in historico if _texto(linha.get("data")) == data_atual), None
    )
    if not hoje or not linha_atual:
        return {"atual": None, "comparacoes": []}

    comparacoes: list[dict] = []
    datas_usadas: set[str] = set()
    for rotulo, dias in JANELAS:
        referencia = busca_referencia(historico, hoje, dias)
        if not referencia:
            continue
        data_ref = _texto(referencia.get("data"))
        # Com poucos dias de histórico, as janelas caem na mesma linha; mostra uma só.
        if data_ref in datas_usadas or data_ref == data_atual:
            continue
        datas_usadas.add(data_ref)
        comparacoes.append({
            # `janela` é o intervalo pedido; `dias` é a distância real até a coleta
            # encontrada, que pode ser maior quando faltam dias no histórico.
            "janela": rotulo,
            "data": data_ref,
            "dias": (hoje - _data(data_ref)).days,
            "deltas": calcula_deltas(linha_atual, referencia, CAMPOS_TENDENCIA),
        })

    return {
        "atual": {campo: numero(linha_atual.get(campo)) for campo in CAMPOS_TENDENCIA},
        "comparacoes": comparacoes,
    }


def crescimento_videos(
    historico_videos: list[dict], data_atual: str, limite: int = 5
) -> dict:
    """Quanto cada vídeo da amostra cresceu desde a coleta anterior."""
    hoje = _data(data_atual)
    if not hoje:
        return {"referencia": None, "destaques": [], "novos": []}

    datas = sorted({_data(l.get("data")) for l in historico_videos if _data(l.get("data"))})
    anteriores = [d for d in datas if d < hoje]
    if not anteriores:
        return {"referencia": None, "destaques": [], "novos": []}

    referencia = anteriores[-1]
    ref_texto = referencia.isoformat()
    antes = {
        l["video_id"]: l
        for l in historico_videos
        if _texto(l.get("data")) == ref_texto and l.get("video_id")
    }
    agora = [l for l in historico_videos if _texto(l.get("data")) == data_atual]

    destaques: list[dict] = []
    novos: list[dict] = []
    for linha in agora:
        anterior = antes.get(_texto(linha.get("video_id")))
        if not anterior:
            novos.append({
                "video_id": linha.get("video_id"),
                "titulo": linha.get("titulo"),
                "visualizacoes": numero(linha.get("visualizacoes")),
            })
            continue
        views_agora, views_antes = numero(linha.get("visualizacoes")), numero(anterior.get("visualizacoes"))
        if views_agora is None or views_antes is None:
            continue
        delta = views_agora - views_antes
        if delta <= 0:
            continue
        destaques.append({
            "video_id": linha.get("video_id"),
            "titulo": linha.get("titulo"),
            "visualizacoes": views_agora,
            "delta_visualizacoes": delta,
            "delta_curtidas": (
                None if numero(linha.get("curtidas")) is None or numero(anterior.get("curtidas")) is None
                else numero(linha.get("curtidas")) - numero(anterior.get("curtidas"))
            ),
        })

    destaques.sort(key=lambda v: v["delta_visualizacoes"], reverse=True)
    return {"referencia": ref_texto, "destaques": destaques[:limite], "novos": novos}
