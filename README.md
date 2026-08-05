# Daily GitHub Activity

Repositório de automações e relatórios gerados por rotinas do Claude Code.

## Relatórios do canal do YouTube

Coleta diária de métricas públicas do canal usando **Playwright + GitHub Actions**.

| Caminho | O que é |
|---|---|
| `scripts/youtube_channel_scraper.py` | O coletor: abre o canal em um Chromium headless e extrai os dados |
| `scripts/test_parsers.py` | Testes das funções de parsing (offline, sem navegador) |
| `.github/workflows/youtube-channel-report.yml` | Workflow que roda a coleta e publica o relatório |
| `reports/youtube/` | Onde os relatórios são gravados |

### O que é coletado

**Do canal:** nome, `@handle`, ID, número de inscritos, total de vídeos publicados,
visualizações totais, descrição completa, país, data de criação, links externos
(Twitch, Instagram, TikTok…), avatar e banner.

**De cada vídeo recente:** título, URL, duração, data de publicação, visualizações,
curtidas, número de comentários, descrição, tags e categoria.

**Consolidado da amostra:** somas e médias de visualizações/curtidas/comentários,
taxa de engajamento e destaques (vídeo mais visto e mais curtido).

> A contagem de curtidas e comentários existe por vídeo — o YouTube não expõe um
> total de curtidas no nível do canal. Por isso o relatório soma os vídeos analisados.

### Saída

Para cada execução, em `reports/youtube/`:

- `AAAA-MM-DD.md` — relatório legível
- `AAAA-MM-DD.json` — dados estruturados
- `latest.json` — cópia do último JSON, para consumo por outras automações

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
| `--max-videos` | `10` | Quantos vídeos recentes detalhar |
| `--output-dir` | `reports/youtube` | Onde gravar o relatório |
| `--sem-comentarios` | desligado | Pula a contagem de comentários (bem mais rápido) |
| `--com-imagens` | desligado | Carrega imagens/fontes/mídia (por padrão são bloqueadas) |
| `--headful` | desligado | Abre o navegador com interface, para depurar |
| `--lang` | `pt-BR` | Idioma da interface do YouTube |
| `--timeout` | `60000` | Timeout de navegação, em milissegundos |

Os testes de parsing rodam sem rede e sem navegador:

```bash
python scripts/test_parsers.py
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
