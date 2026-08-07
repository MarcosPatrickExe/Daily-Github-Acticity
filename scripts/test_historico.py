#!/usr/bin/env python3
"""Testes do histórico, do diagnóstico e do gráfico.

Não abrem navegador e não acessam a rede — o único I/O é em diretório temporário:

    python scripts/test_historico.py
"""

from __future__ import annotations

import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from diagnostico import avalia_saude, resumo_texto  # noqa: E402
from grafico import MAX_PONTOS, _escapa, extrai_serie, ganhos_por_dia, monta_svg  # noqa: E402
from historico import (  # noqa: E402
    COLUNAS_CANAL,
    busca_referencia,
    calcula_deltas,
    contagem_por_tipo,
    crescimento_videos,
    data_anterior,
    grava_csv,
    le_csv,
    linha_do_canal,
    linhas_dos_videos,
    monta_tendencia,
    numero,
    upsert,
)

RELATORIO = {
    "gerado_em": "2026-08-07T15:30:00+00:00",
    "canal": {"inscritos": 122, "total_videos": 205, "visualizacoes_totais": 39_500},
    "resumo": {
        "videos_analisados": 2,
        "visualizacoes_soma": 300,
        "curtidas_soma": 12,
        "comentarios_soma": 3,
        "taxa_engajamento_pct": 5.0,
    },
    "videos": [
        {"video_id": "aaa", "titulo": "A", "visualizacoes": 100, "curtidas": 5, "comentarios": 1},
        {"video_id": "bbb", "titulo": "B", "visualizacoes": 200, "curtidas": 7, "comentarios": 2},
        {"video_id": "ccc", "titulo": "C", "erro": "timeout"},
    ],
    "avisos": [],
}

SERIE = [
    {"data": "2026-07-08", "inscritos": "110", "total_videos": "195", "visualizacoes_totais": "36000"},
    {"data": "2026-07-31", "inscritos": "115", "total_videos": "200", "visualizacoes_totais": "38000"},
    {"data": "2026-08-06", "inscritos": "120", "total_videos": "204", "visualizacoes_totais": "39300"},
    {"data": "2026-08-07", "inscritos": "122", "total_videos": "205", "visualizacoes_totais": "39500"},
]

SERIE_VIDEOS = [
    {"data": "2026-08-06", "video_id": "aaa", "titulo": "A", "visualizacoes": "80", "curtidas": "4"},
    {"data": "2026-08-06", "video_id": "bbb", "titulo": "B", "visualizacoes": "195", "curtidas": "7"},
    {"data": "2026-08-07", "video_id": "aaa", "titulo": "A", "visualizacoes": "100", "curtidas": "5"},
    {"data": "2026-08-07", "video_id": "bbb", "titulo": "B", "visualizacoes": "200", "curtidas": "7"},
    {"data": "2026-08-07", "video_id": "zzz", "titulo": "Z", "visualizacoes": "10", "curtidas": "0"},
]


def executa() -> int:
    falhas: list[str] = []

    def checa(nome: str, obtido, esperado) -> None:
        if obtido != esperado:
            falhas.append(f"{nome}: obtido {obtido!r}, esperado {esperado!r}")

    # --- conversões -------------------------------------------------------
    checa("numero(int)", numero("42"), 42)
    checa("numero(float)", numero("5.5"), 5.5)
    checa("numero(vazio)", numero(""), None)
    checa("numero(None)", numero(None), None)
    checa("numero(texto)", numero("abc"), None)

    # --- montagem das linhas ---------------------------------------------
    linha = linha_do_canal(RELATORIO, "2026-08-07")
    checa("linha.data", linha["data"], "2026-08-07")
    checa("linha.inscritos", linha["inscritos"], 122)
    checa("linha.comentarios_soma", linha["comentarios_soma"], 3)

    videos = linhas_dos_videos(RELATORIO, "2026-08-07")
    checa("linhas_videos.qtd (ignora com erro)", len(videos), 2)
    checa("linhas_videos.id", videos[0]["video_id"], "aaa")

    # --- upsert -----------------------------------------------------------
    atualizado = upsert(
        [{"data": "2026-08-07", "inscritos": "1"}, {"data": "2026-08-06", "inscritos": "9"}],
        [{"data": "2026-08-07", "inscritos": "122"}],
        ("data",),
    )
    checa("upsert.qtd (substitui o dia)", len(atualizado), 2)
    checa("upsert.ordem", [l["data"] for l in atualizado], ["2026-08-06", "2026-08-07"])
    checa("upsert.valor", atualizado[1]["inscritos"], "122")

    por_video = upsert(
        [{"data": "2026-08-07", "video_id": "aaa", "visualizacoes": "1"}],
        [{"data": "2026-08-07", "video_id": "aaa", "visualizacoes": "100"},
         {"data": "2026-08-07", "video_id": "bbb", "visualizacoes": "200"}],
        ("data", "video_id"),
    )
    checa("upsert_video.qtd", len(por_video), 2)
    checa("upsert_video.valor", por_video[0]["visualizacoes"], "100")

    # --- referências e deltas --------------------------------------------
    hoje = date(2026, 8, 7)
    checa("busca_referencia(1d)", busca_referencia(SERIE, hoje, 1)["data"], "2026-08-06")
    checa("busca_referencia(7d)", busca_referencia(SERIE, hoje, 7)["data"], "2026-07-31")
    checa("busca_referencia(sem histórico)", busca_referencia([], hoje, 1), None)

    deltas = calcula_deltas(SERIE[3], SERIE[2], ("inscritos", "visualizacoes_totais"))
    checa("deltas.inscritos", deltas["inscritos"], 2)
    checa("deltas.views", deltas["visualizacoes_totais"], 200)
    checa("deltas.campo ausente", calcula_deltas({}, {}, ("x",))["x"], None)

    # --- tendência --------------------------------------------------------
    tendencia = monta_tendencia(SERIE, "2026-08-07")
    checa("tendencia.atual", tendencia["atual"]["inscritos"], 122)
    checa("tendencia.janelas", [c["data"] for c in tendencia["comparacoes"]],
          ["2026-08-06", "2026-07-31", "2026-07-08"])
    checa("tendencia.dias", tendencia["comparacoes"][0]["dias"], 1)
    checa("tendencia.delta", tendencia["comparacoes"][0]["deltas"]["visualizacoes_totais"], 200)
    checa("tendencia.primeira coleta", monta_tendencia(SERIE[:1], "2026-07-08")["comparacoes"], [])
    checa("tendencia.data ausente", monta_tendencia(SERIE, "2030-01-01")["atual"], None)

    # Com uma única coleta anterior, as três janelas caem na mesma data: mostra uma só.
    uma_so = monta_tendencia(
        [{"data": "2026-08-06", "inscritos": "120"}, {"data": "2026-08-07", "inscritos": "122"}],
        "2026-08-07",
    )
    checa("tendencia.sem janelas repetidas", len(uma_so["comparacoes"]), 1)

    # Dia sem coleta (runner que não veio, por exemplo): a janela de 1 dia cai na
    # coleta anterior disponível e `dias` mostra a distância real.
    com_buraco = monta_tendencia(
        [{"data": "2026-08-05", "inscritos": "120"}, {"data": "2026-08-07", "inscritos": "122"}],
        "2026-08-07",
    )
    checa("tendencia.buraco.janela", com_buraco["comparacoes"][0]["janela"], "1 dia")
    checa("tendencia.buraco.data", com_buraco["comparacoes"][0]["data"], "2026-08-05")
    checa("tendencia.buraco.dias reais", com_buraco["comparacoes"][0]["dias"], 2)

    # --- crescimento por vídeo -------------------------------------------
    cresc = crescimento_videos(SERIE_VIDEOS, "2026-08-07")
    checa("crescimento.referencia", cresc["referencia"], "2026-08-06")
    checa("crescimento.qtd", len(cresc["destaques"]), 2)
    checa("crescimento.ordem", cresc["destaques"][0]["video_id"], "aaa")
    checa("crescimento.delta", cresc["destaques"][0]["delta_visualizacoes"], 20)
    checa("crescimento.delta_curtidas", cresc["destaques"][1]["delta_curtidas"], 0)
    checa("crescimento.novos", [v["video_id"] for v in cresc["novos"]], ["zzz"])
    checa("crescimento.primeira coleta", crescimento_videos(SERIE_VIDEOS, "2026-08-06")["destaques"], [])

    # --- contagem por formato --------------------------------------------
    com_tipos = [
        {"data": "2026-08-06", "video_id": "a", "tipo": "video"},
        {"data": "2026-08-06", "video_id": "b", "tipo": "short"},
        {"data": "2026-08-06", "video_id": "c", "tipo": "short"},
        {"data": "2026-08-07", "video_id": "a", "tipo": "video"},
    ]
    checa("contagem_por_tipo", contagem_por_tipo(com_tipos, "2026-08-06"),
          {"video": 1, "short": 2})
    checa("contagem_por_tipo(sem tipo vira vídeo)",
          contagem_por_tipo([{"data": "2026-08-07", "video_id": "x"}], "2026-08-07"), {"video": 1})
    checa("contagem_por_tipo(data sem coleta)", contagem_por_tipo(com_tipos, "2026-01-01"), {})
    checa("data_anterior", data_anterior(com_tipos, "2026-08-07"), "2026-08-06")
    checa("data_anterior(primeira coleta)", data_anterior(com_tipos, "2026-08-06"), None)

    # --- CSV de ida e volta ----------------------------------------------
    with tempfile.TemporaryDirectory() as pasta:
        caminho = Path(pasta) / "historico.csv"
        checa("le_csv(inexistente)", le_csv(caminho), [])
        grava_csv(caminho, COLUNAS_CANAL, [linha_do_canal(RELATORIO, "2026-08-07")])
        lido = le_csv(caminho)
        checa("csv.qtd", len(lido), 1)
        checa("csv.inscritos", lido[0]["inscritos"], "122")
        checa("csv.None vira vazio",
              le_csv(caminho)[0]["taxa_engajamento_pct"], "5.0")
        checa("csv.sem CRLF", "\r" in caminho.read_text(encoding="utf-8"), False)

    # --- diagnóstico ------------------------------------------------------
    saude = avalia_saude(RELATORIO)
    checa("saude.detecta vídeo com erro",
          any("falharam" in p for p in saude["problemas"]), True)

    limpo = {**RELATORIO, "videos": RELATORIO["videos"][:2]}
    checa("saude.ok", avalia_saude(limpo)["ok"], True)
    checa("saude.sem problemas", avalia_saude(limpo)["problemas"], [])

    # Regressão do bug real: a contagem de comentários voltou vazia para todos.
    sem_comentarios = {
        **limpo,
        "videos": [{**v, "comentarios": None} for v in limpo["videos"]],
    }
    resultado = avalia_saude(sem_comentarios)
    checa("saude.detecta métrica sumida", resultado["ok"], False)
    checa("saude.aponta comentários",
          any("comentários" in p for p in resultado["problemas"]), True)

    checa("saude.canal incompleto",
          any("inscritos" in p for p in avalia_saude({"canal": {}, "videos": []})["problemas"]), True)
    checa("saude.sem vídeos",
          any("Nenhum vídeo" in p for p in avalia_saude({"canal": {}, "videos": []})["problemas"]), True)
    checa("saude.avisos viram problema",
          any("aviso" in p for p in avalia_saude({**limpo, "avisos": ["x"]})["problemas"]), True)

    # Visualizações totais são acumulativas: cair indica leitura errada.
    regressao = avalia_saude(limpo, {"comparacoes": [
        {"janela": "1 dia", "data": "2026-08-06", "dias": 1,
         "deltas": {"visualizacoes_totais": -500}},
    ]})
    checa("saude.detecta queda de views",
          any("caíram" in p for p in regressao["problemas"]), True)

    # Um formato inteiro que some entre uma coleta e outra é aba quebrada.
    sumiu = avalia_saude(limpo, None, {"video": 2, "short": 5})
    checa("saude.detecta formato sumido", sumiu["ok"], False)
    checa("saude.aponta o formato",
          any("'short'" in p for p in sumiu["problemas"]), True)
    # Formato que nunca existiu não deve virar alarme todo dia.
    checa("saude.formato sempre ausente não alarma",
          avalia_saude(limpo, None, {"video": 2, "short": 0})["ok"], True)

    checa("resumo_texto(ok)", "✅" in resumo_texto({"ok": True, "problemas": []}), True)
    checa("resumo_texto(problema)", "⚠️" in resumo_texto(resultado), True)

    # --- gráfico ----------------------------------------------------------
    checa("grafico.extrai_serie", extrai_serie(SERIE, "inscritos"),
          [(date(2026, 7, 8), 110.0), (date(2026, 7, 31), 115.0),
           (date(2026, 8, 6), 120.0), (date(2026, 8, 7), 122.0)])
    checa("grafico.ignora valor vazio",
          extrai_serie([{"data": "2026-08-07", "inscritos": ""}], "inscritos"), [])
    checa("grafico.ignora data inválida",
          extrai_serie([{"data": "ontem", "inscritos": "1"}], "inscritos"), [])
    checa("grafico.limita pontos",
          len(extrai_serie(
              [{"data": f"2026-01-{d:02d}", "inscritos": "1"} for d in range(1, 32)] +
              [{"data": f"2026-02-{d:02d}", "inscritos": "1"} for d in range(1, 29)] +
              [{"data": f"2026-03-{d:02d}", "inscritos": "1"} for d in range(1, 10)],
              "inscritos")), MAX_PONTOS)

    # Um dia sem coleta não pode virar pico: o ganho é dividido pelo intervalo.
    ganhos = ganhos_por_dia([(date(2026, 8, 1), 100.0), (date(2026, 8, 3), 300.0)])
    checa("grafico.ganho normalizado por dia", ganhos, [(date(2026, 8, 3), 100.0)])
    checa("grafico.ganho de série curta", ganhos_por_dia([(date(2026, 8, 1), 1.0)]), [])

    svg_vazio = monta_svg([{"data": "2026-08-05", "inscritos": "120"}])
    checa("grafico.1 coleta explica em vez de desenhar",
          "não há coletas suficientes" in svg_vazio, True)
    checa("grafico.sem coletas", "<svg" in monta_svg([]), True)

    svg = monta_svg(SERIE, "07/08/2026")
    checa("grafico.desenha inscritos", "Inscritos" in svg, True)
    checa("grafico.desenha views", "Visualizações totais" in svg, True)
    checa("grafico.desenha ganho diário", "ganhas por dia" in svg, True)
    checa("grafico.rodapé", "Atualizado em 07/08/2026" in svg, True)
    checa("grafico.tema escuro", "prefers-color-scheme: dark" in svg, True)
    try:
        ET.fromstring(svg)
        ET.fromstring(svg_vazio)
    except ET.ParseError as erro:
        falhas.append(f"grafico: SVG inválido ({erro})")

    checa("grafico.escapa XML", _escapa('a<b>&"'), "a&lt;b&gt;&amp;&quot;")

    if falhas:
        print(f"❌ {len(falhas)} falha(s):")
        for falha in falhas:
            print(f"  - {falha}")
        return 1

    print("✅ Todos os testes de histórico e diagnóstico passaram.")
    return 0


if __name__ == "__main__":
    raise SystemExit(executa())
