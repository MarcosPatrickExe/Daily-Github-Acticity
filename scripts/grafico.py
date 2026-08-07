#!/usr/bin/env python3
"""Gera o gráfico de evolução do canal em SVG, a partir do histórico.

SVG escrito à mão de propósito: o relatório roda num runner do GitHub Actions e
não vale puxar matplotlib (e uma cadeia de dependências) para desenhar duas
séries. A saída é um arquivo só, versionado junto com os relatórios e exibido
direto no README.

As cores respondem a `prefers-color-scheme`, então o gráfico continua legível
em quem lê o repositório no tema escuro.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Sequence

LARGURA = 820
ALTURA = 360
MARGEM_ESQ = 62
MARGEM_DIR = 18
MARGEM_TOPO = 34
ESPACO_ENTRE = 46
ALTURA_PAINEL = 108

# Máximo de coletas desenhadas. Passando disso, o gráfico mostra as mais recentes.
MAX_PONTOS = 60


def _escapa(texto: str) -> str:
    return (
        str(texto)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _numero(valor) -> float | None:
    if valor is None or valor == "":
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _data(valor) -> date | None:
    try:
        return datetime.strptime(str(valor).strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _rotulo(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}"


def _formata(valor: float) -> str:
    inteiro = int(round(valor))
    return f"{inteiro:,}".replace(",", ".")


def extrai_serie(historico: Sequence[dict], campo: str) -> list[tuple[date, float]]:
    """Pares (data, valor) ordenados, ignorando linhas sem o campo."""
    pontos = []
    for linha in historico:
        d, v = _data(linha.get("data")), _numero(linha.get(campo))
        if d is not None and v is not None:
            pontos.append((d, v))
    pontos.sort(key=lambda p: p[0])
    return pontos[-MAX_PONTOS:]


def ganhos_por_dia(pontos: Sequence[tuple[date, float]]) -> list[tuple[date, float]]:
    """Diferença entre coletas consecutivas, normalizada por dia decorrido.

    Dividir pelo intervalo evita que um dia sem coleta apareça como um pico —
    o ganho acumulado de dois dias vira a média diária dos dois.
    """
    ganhos = []
    for (d_antes, v_antes), (d_agora, v_agora) in zip(pontos, pontos[1:]):
        dias = max((d_agora - d_antes).days, 1)
        ganhos.append((d_agora, (v_agora - v_antes) / dias))
    return ganhos


def _escala(valores: Sequence[float]) -> tuple[float, float]:
    """Limites do eixo Y, com uma folga para o traço não encostar na borda."""
    if not valores:
        return 0.0, 1.0
    menor, maior = min(valores), max(valores)
    if menor == maior:
        # Série constante: centraliza a linha no painel.
        folga = abs(menor) * 0.1 or 1.0
        return menor - folga, maior + folga
    folga = (maior - menor) * 0.15
    return menor - folga, maior + folga


def _pontos_x(quantidade: int) -> list[float]:
    """X igualmente espaçados: o eixo é a sequência de coletas, não o calendário."""
    util = LARGURA - MARGEM_ESQ - MARGEM_DIR
    if quantidade <= 1:
        return [MARGEM_ESQ + util / 2]
    return [MARGEM_ESQ + util * i / (quantidade - 1) for i in range(quantidade)]


def _painel_linha(pontos, topo: float, titulo: str, classe: str) -> list[str]:
    valores = [v for _, v in pontos]
    minimo, maximo = _escala(valores)
    intervalo = maximo - minimo or 1.0
    xs = _pontos_x(len(pontos))
    ys = [topo + ALTURA_PAINEL - (v - minimo) / intervalo * ALTURA_PAINEL for v in valores]

    partes = [
        f'<text class="titulo" x="{MARGEM_ESQ}" y="{topo - 10:.1f}">{_escapa(titulo)}</text>',
        f'<line class="eixo" x1="{MARGEM_ESQ}" y1="{topo + ALTURA_PAINEL:.1f}" '
        f'x2="{LARGURA - MARGEM_DIR}" y2="{topo + ALTURA_PAINEL:.1f}" />',
        f'<text class="escala" x="{MARGEM_ESQ - 8}" y="{topo + 4:.1f}" text-anchor="end">'
        f'{_escapa(_formata(maximo))}</text>',
        f'<text class="escala" x="{MARGEM_ESQ - 8}" y="{topo + ALTURA_PAINEL:.1f}" '
        f'text-anchor="end">{_escapa(_formata(minimo))}</text>',
    ]

    traco = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    area = f"{xs[0]:.1f},{topo + ALTURA_PAINEL:.1f} {traco} {xs[-1]:.1f},{topo + ALTURA_PAINEL:.1f}"
    partes.append(f'<polygon class="area {classe}" points="{area}" />')
    partes.append(f'<polyline class="linha {classe}" points="{traco}" />')
    for x, y in zip(xs, ys):
        partes.append(f'<circle class="ponto {classe}" cx="{x:.1f}" cy="{y:.1f}" r="2.6" />')

    # Só o último valor recebe rótulo: é o número que interessa de relance.
    partes.append(
        f'<text class="valor {classe}" x="{xs[-1]:.1f}" y="{ys[-1] - 9:.1f}" '
        f'text-anchor="end">{_escapa(_formata(valores[-1]))}</text>'
    )
    return partes


def _painel_barras(pontos, topo: float, titulo: str, classe: str) -> list[str]:
    valores = [v for _, v in pontos]
    maximo = max(valores + [0]) or 1.0
    minimo = min(valores + [0])
    intervalo = (maximo - minimo) or 1.0
    xs = _pontos_x(len(pontos))
    largura_barra = max(3.0, min(26.0, (LARGURA - MARGEM_ESQ - MARGEM_DIR) / max(len(xs), 1) * 0.6))
    base = topo + ALTURA_PAINEL - (0 - minimo) / intervalo * ALTURA_PAINEL

    partes = [
        f'<text class="titulo" x="{MARGEM_ESQ}" y="{topo - 10:.1f}">{_escapa(titulo)}</text>',
        f'<line class="eixo" x1="{MARGEM_ESQ}" y1="{base:.1f}" '
        f'x2="{LARGURA - MARGEM_DIR}" y2="{base:.1f}" />',
        f'<text class="escala" x="{MARGEM_ESQ - 8}" y="{topo + 4:.1f}" text-anchor="end">'
        f'{_escapa(_formata(maximo))}</text>',
    ]

    for x, valor in zip(xs, valores):
        y = topo + ALTURA_PAINEL - (valor - minimo) / intervalo * ALTURA_PAINEL
        altura = abs(base - y)
        partes.append(
            f'<rect class="barra {classe}" x="{x - largura_barra / 2:.1f}" '
            f'y="{min(y, base):.1f}" width="{largura_barra:.1f}" '
            f'height="{max(altura, 1.0):.1f}" rx="2" />'
        )
    return partes


def _eixo_datas(pontos, y: float) -> list[str]:
    """Rótulos de data espaçados para não se empilharem quando a série cresce."""
    xs = _pontos_x(len(pontos))
    passo = max(1, len(pontos) // 8)
    partes = []
    for i, ((d, _), x) in enumerate(zip(pontos, xs)):
        if i % passo and i != len(pontos) - 1:
            continue
        partes.append(
            f'<text class="escala" x="{x:.1f}" y="{y:.1f}" text-anchor="middle">'
            f'{_escapa(_rotulo(d))}</text>'
        )
    return partes


ESTILO = """
  .fundo { fill: #ffffff; }
  .titulo { font: 600 12px system-ui, -apple-system, Segoe UI, sans-serif; fill: #1f2328; }
  .escala { font: 10px system-ui, -apple-system, Segoe UI, sans-serif; fill: #6a737d; }
  .valor  { font: 600 11px system-ui, -apple-system, Segoe UI, sans-serif; }
  .rodape { font: 10px system-ui, -apple-system, Segoe UI, sans-serif; fill: #8b949e; }
  .eixo { stroke: #d0d7de; stroke-width: 1; }
  .linha { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
  .inscritos { stroke: #1f6feb; }
  .inscritos.area { fill: #1f6feb; opacity: 0.10; stroke: none; }
  .inscritos.ponto, .inscritos.valor { fill: #1f6feb; stroke: none; }
  .views { stroke: #8250df; }
  .views.area { fill: #8250df; opacity: 0.10; stroke: none; }
  .views.ponto, .views.valor { fill: #8250df; stroke: none; }
  .barra.ganho { fill: #1a7f37; }
  @media (prefers-color-scheme: dark) {
    .fundo { fill: #0d1117; }
    .titulo { fill: #e6edf3; }
    .escala, .rodape { fill: #8b949e; }
    .eixo { stroke: #30363d; }
    .inscritos { stroke: #58a6ff; }
    .inscritos.area { fill: #58a6ff; }
    .inscritos.ponto, .inscritos.valor { fill: #58a6ff; }
    .views { stroke: #bc8cff; }
    .views.area { fill: #bc8cff; }
    .views.ponto, .views.valor { fill: #bc8cff; }
    .barra.ganho { fill: #3fb950; }
  }
"""


def _svg(corpo: list[str], altura: int = ALTURA) -> str:
    return "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {LARGURA} {altura}" '
        f'width="{LARGURA}" height="{altura}" role="img" '
        f'aria-label="Evolução do canal no YouTube">',
        f"<style>{ESTILO}</style>",
        f'<rect class="fundo" width="{LARGURA}" height="{altura}" rx="6" />',
        *corpo,
        "</svg>",
        "",
    ])


def monta_svg(historico: Sequence[dict], gerado_em: str | None = None) -> str:
    """Desenha inscritos, visualizações totais e ganho diário de visualizações."""
    inscritos = extrai_serie(historico, "inscritos")
    views = extrai_serie(historico, "visualizacoes_totais")

    if len(inscritos) < 2 and len(views) < 2:
        return _svg([
            f'<text class="titulo" x="{LARGURA / 2:.0f}" y="86" text-anchor="middle">'
            "Ainda não há coletas suficientes para desenhar a evolução.</text>",
            f'<text class="escala" x="{LARGURA / 2:.0f}" y="108" text-anchor="middle">'
            "O gráfico aparece a partir da segunda coleta.</text>",
        ], altura=170)

    corpo: list[str] = []
    topo = MARGEM_TOPO

    if len(inscritos) >= 2:
        corpo += _painel_linha(inscritos, topo, "Inscritos", "inscritos")
        corpo += _eixo_datas(inscritos, topo + ALTURA_PAINEL + 15)
        topo += ALTURA_PAINEL + ESPACO_ENTRE

    if len(views) >= 2:
        corpo += _painel_linha(views, topo, "Visualizações totais", "views")
        corpo += _eixo_datas(views, topo + ALTURA_PAINEL + 15)
        topo += ALTURA_PAINEL + ESPACO_ENTRE

        ganhos = ganhos_por_dia(views)
        if ganhos:
            corpo += _painel_barras(ganhos, topo, "Visualizações ganhas por dia", "ganho")
            corpo += _eixo_datas(ganhos, topo + ALTURA_PAINEL + 15)
            topo += ALTURA_PAINEL + ESPACO_ENTRE

    altura = int(topo + 6)
    if gerado_em:
        corpo.append(
            f'<text class="rodape" x="{LARGURA - MARGEM_DIR}" y="{altura - 8}" '
            f'text-anchor="end">Atualizado em {_escapa(gerado_em)}</text>'
        )
    return _svg(corpo, altura=altura)
