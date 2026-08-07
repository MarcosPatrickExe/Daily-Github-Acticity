# Daily GitHub Activity

Repositório de automações e relatórios gerados por rotinas do Claude Code.

## Relatórios do canal do YouTube

Coleta diária de métricas públicas do canal usando **Playwright + GitHub Actions**.

| Caminho | O que é |
|---|---|
| `scripts/youtube_channel_scraper.py` | O coletor: abre o canal em um Chromium headless e extrai os dados |
| `scripts/historico.py` | Série temporal em CSV e cálculo das tendências |
| `scripts/diagnostico.py` | Verificações de sanidade sobre o relatório coletado |
| `scripts/reconstroi_historico.py` | Recria os CSVs de histórico a partir dos JSONs diários |
| `scripts/test_parsers.py` | Testes das funções de parsing (offline, sem navegador) |
| `scripts/test_historico.py` | Testes do histórico e do diagnóstico (offline) |
| `scripts/fixtures/` | Recorte real do `ytInitialData` das abas, usado nos testes |
| `.github/workflows/youtube-channel-report.yml` | Workflow que roda a coleta e publica o relatório |
| `reports/youtube/` | Onde os relatórios e o histórico são gravados |

### O que é coletado

**Do canal:** nome, `@handle`, ID, número de inscritos, total de vídeos publicados,
visualizações totais, descrição completa, país, data de criação, links externos
(Twitch, Instagram, TikTok…), avatar e banner.

**De cada item recente:** título, URL, duração, data de publicação, visualizações,
curtidas, número de comentários, descrição, tags e categoria. São coletados os três
formatos, cada um da sua aba e marcados com um `tipo`:

| Formato | Aba | `tipo` | Padrão |
|---|---|---|---|
| Vídeos longos | `/videos` | `video` | 10 |
| Shorts | `/shorts` | `short` | 5 |
| Lives e transmissões | `/streams` | `live` | 3 |

**Playlists:** título, link e quantidade de vídeos de cada uma (aba `/playlists`).

**Consolidado da amostra:** somas e médias de visualizações/curtidas/comentários,
taxa de engajamento, destaques (mais visto e mais curtido) e uma quebra **por
formato** — um Short e um vídeo longo rendem números muito diferentes, e a média
conjunta esconde isso.

> A contagem de curtidas e comentários existe por vídeo — o YouTube não expõe um
> total de curtidas no nível do canal. Por isso o relatório soma os vídeos analisados.

**Tendência:** cada coleta é comparada com as de 1, 7 e 30 dias atrás — quantos
inscritos e visualizações o canal ganhou, o ritmo médio por dia e quais vídeos
mais cresceram desde a coleta anterior.

### Saída

Para cada execução, em `reports/youtube/`:

- `AAAA-MM-DD.md` — relatório legível
- `AAAA-MM-DD.json` — dados estruturados
- `latest.json` — cópia do último JSON, para consumo por outras automações
- `historico.csv` — uma linha por coleta, com as métricas do canal
- `historico_videos.csv` — uma linha por vídeo por coleta

Os dois CSVs são acumulativos e reexecutar a coleta no mesmo dia **atualiza** a
linha do dia em vez de duplicá-la. Eles são derivados dos JSONs diários — se
algum se perder, `python scripts/reconstroi_historico.py` reconstrói ambos.

### Diagnóstico da coleta

Uma coleta pode terminar sem erro e ainda assim publicar dados errados — foi o
que aconteceu quando a contagem de comentários passou a voltar vazia para todos
os vídeos e o workflow seguiu verde. Por isso todo relatório passa por
verificações de sanidade antes de ser considerado bom:

- o canal veio sem inscritos, total de vídeos ou visualizações totais;
- nenhum vídeo foi coletado, ou parte deles falhou;
- mais da metade da amostra está sem alguma métrica (sinal de mudança de layout);
- um formato inteiro sumiu — havia Shorts na coleta anterior e nenhum nesta;
- a coleta registrou avisos;
- as visualizações totais **caíram** — esse número só deveria crescer.

Achando qualquer um desses, o coletor termina com código 2 (com
`--falhar-com-problemas`), a execução do Actions fica vermelha e o workflow abre
uma issue com a lista de problemas. O relatório do dia é publicado mesmo assim,
com a seção *Diagnóstico da coleta* explicando o que destoou.

### Rodando localmente

```bash
pip install -r scripts/requirements.txt
python -m playwright install --with-deps chromium

python scripts/youtube_channel_scraper.py \
  --channel https://youtube.com/@patrickson_plays \
  --max-videos 10
```

Opções úteis:

| Flag | Padrão | Para que serve |
|---|---|---|
| `--channel` | `@patrickson_plays` | URL ou `@handle` do canal |
| `--max-videos` | `10` | Quantos vídeos longos detalhar (`0` desliga a aba) |
| `--max-shorts` | `5` | Quantos Shorts detalhar (`0` desliga) |
| `--max-lives` | `3` | Quantas lives detalhar (`0` desliga) |
| `--max-playlists` | `25` | Quantas playlists listar (`0` desliga) |
| `--output-dir` | `reports/youtube` | Onde gravar o relatório |
| `--sem-comentarios` | desligado | Pula a contagem de comentários (bem mais rápido) |
| `--falhar-com-problemas` | desligado | Termina com código 2 se o diagnóstico acusar problema |
| `--com-imagens` | desligado | Carrega imagens/fontes/mídia (por padrão são bloqueadas) |
| `--headful` | desligado | Abre o navegador com interface, para depurar |
| `--lang` | `pt-BR` | Idioma da interface do YouTube |
| `--timeout` | `60000` | Timeout de navegação, em milissegundos |

Os testes rodam sem rede e sem navegador:

```bash
python scripts/test_parsers.py    # parsing das páginas do YouTube
python scripts/test_historico.py  # histórico, tendências e diagnóstico
```

### Rodando pelo GitHub Actions

- **Sob demanda:** aba *Actions* → *Relatório do canal do YouTube* → *Run workflow*
  (dá para trocar o canal e a quantidade de vídeos na hora).
- **Agendado:** todo dia às **12:30** de Brasília (`30 15 * * *`, já que o cron do
  GitHub é sempre em UTC e Brasília é UTC-3).

O workflow commita o relatório de volta na branch em que roda. Ele precisa de
`permissions: contents: write`, já declarado no arquivo.

> ⚠️ O GitHub só dispara workflows agendados a partir da **branch padrão** do
> repositório. Enquanto o workflow não estiver na branch padrão, apenas a execução
> manual (*Run workflow*) funciona.

### Como o coletor funciona

Em vez de depender de seletores CSS (que o YouTube muda com frequência), o script lê
o objeto `ytInitialData` que o próprio YouTube injeta na página — a mesma estrutura
que alimenta a interface. Os seletores de DOM são usados só onde o dado não existe no
carregamento inicial, como a contagem de comentários, que só chega depois do scroll —
e é justamente por isso que a coleta precisa de um navegador de verdade.

Falhas parciais não derrubam a execução: cada problema vira um aviso na seção
*Avisos da coleta* do relatório, e o restante dos dados é publicado normalmente.
