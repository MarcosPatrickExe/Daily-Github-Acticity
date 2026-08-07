#!/usr/bin/env python3
"""Verificações de sanidade sobre o relatório recém-coletado.

Motivação concreta: uma coleta pode terminar sem erro nenhum e ainda assim
publicar dados errados — foi o que aconteceu quando a contagem de comentários
passou a voltar vazia para todos os vídeos e o workflow seguiu verde. Um
número que some é tão grave quanto uma exceção, e precisa ser visível.

As funções são puras: recebem o relatório e devolvem a lista de problemas.
"""

from __future__ import annotations

from typing import Any, Sequence

# Acima desta fração de vídeos sem uma métrica, o caso deixa de ser "esse vídeo
# não tem" e passa a ser "o coletor parou de ler".
LIMIAR_FALTANTE = 0.5

CAMPOS_CANAL = (
    ("inscritos", "o número de inscritos"),
    ("total_videos", "o total de vídeos publicados"),
    ("visualizacoes_totais", "as visualizações totais"),
)

CAMPOS_VIDEO = (
    ("visualizacoes", "visualizações"),
    ("curtidas", "curtidas"),
    ("comentarios", "comentários"),
)


def _menor_comparacao(tendencia: dict | None) -> dict | None:
    """A janela mais curta disponível — a melhor para detectar regressão."""
    comparacoes = (tendencia or {}).get("comparacoes") or []
    return min(comparacoes, key=lambda c: c.get("dias") or 0) if comparacoes else None


def avalia_saude(relatorio: dict, tendencia: dict | None = None) -> dict[str, Any]:
    """Devolve {"ok": bool, "problemas": [...]} com o que destoou nesta coleta."""
    problemas: list[str] = []

    canal = relatorio.get("canal") or {}
    for campo, rotulo in CAMPOS_CANAL:
        if canal.get(campo) is None:
            problemas.append(f"O canal veio sem {rotulo}.")

    videos = relatorio.get("videos") or []
    if not videos:
        problemas.append("Nenhum vídeo foi coletado.")
    else:
        com_erro = [v for v in videos if "erro" in v]
        if com_erro:
            problemas.append(
                f"{len(com_erro)} de {len(videos)} vídeos falharam na coleta."
            )

        analisados = [v for v in videos if "erro" not in v]
        for campo, rotulo in CAMPOS_VIDEO:
            faltando = [v for v in analisados if v.get(campo) is None]
            if analisados and len(faltando) > len(analisados) * LIMIAR_FALTANTE:
                problemas.append(
                    f"{len(faltando)} de {len(analisados)} vídeos sem {rotulo} — "
                    "provável mudança no layout do YouTube."
                )

    avisos = relatorio.get("avisos") or []
    if avisos:
        problemas.append(
            f"A coleta registrou {len(avisos)} aviso(s) — veja a seção de avisos."
        )

    # Visualizações totais são acumulativas: cair indica leitura errada, não queda real.
    comparacao = _menor_comparacao(tendencia)
    if comparacao:
        delta = (comparacao.get("deltas") or {}).get("visualizacoes_totais")
        if delta is not None and delta < 0:
            problemas.append(
                f"As visualizações totais caíram {abs(delta)} desde {comparacao['data']} — "
                "esse número só deveria crescer."
            )

    return {"ok": not problemas, "problemas": problemas}


def resumo_texto(saude: dict, titulo: str = "Diagnóstico da coleta") -> str:
    """Versão em Markdown, usada no relatório e no resumo do GitHub Actions."""
    problemas: Sequence[str] = saude.get("problemas") or []
    if not problemas:
        return f"## {titulo}\n\n✅ Nenhum problema detectado nesta coleta.\n"
    itens = "\n".join(f"- {p}" for p in problemas)
    rotulo = "problema detectado" if len(problemas) == 1 else "problemas detectados"
    return f"## {titulo}\n\n⚠️ {len(problemas)} {rotulo}:\n\n{itens}\n"
