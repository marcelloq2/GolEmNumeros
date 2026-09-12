# DESIGN.md — Gol em Números (StatArea)

Especificação de identidade visual para agentes de codificação. Este arquivo
descreve os tokens e regras visuais do projeto — leia antes de gerar ou
alterar qualquer HTML/CSS em `static/index.html` ou telas relacionadas.

## Conceito

Painel de apostas esportivas ao vivo com estética de "terminal de dados
esportivos" — escuro por padrão, denso em números, sem decoração
desnecessária. A referência é um placar de estádio / terminal financeiro,
não um app consumer genérico. Tema claro existe e deve ser igualmente
legível (ver regra de cores abaixo).

Fase de redesign (2026-09): saiu o dourado como cor de marca, entrou o
**verde** (#00e676 / #087f44) como accent de destaque pontual. Sem
gradientes, sem glow/box-shadow decorativo. Emojis decorativos estão sendo
substituídos por ícones SVG de linha inline (`svg.ico`, herdam cor via
`currentColor`) — migração em andamento, nem todo emoji foi trocado ainda.

## Cores

Definidas como custom properties em `:root` (tema escuro, padrão) e
sobrescritas em `html[data-theme="light"]`. **Nunca usar hex fixo em
componente novo** — sempre `var(--nome)`. Cor fixa fora dessas variáveis já
causou ilegibilidade no tema claro (~80 ocorrências corrigidas).

| Token | Escuro | Claro | Uso |
|---|---|---|---|
| `--bg` | `#0b0d0c` | `#f6f5f0` | fundo da página |
| `--bg2` | `#121613` | `#ffffff` | fundo de card/header |
| `--bg3` | `#1a211c` | `#ecead9` | fundo de superfície elevada (hover, input) |
| `--border` | `#2a332c` | `#dedbd0` | bordas e divisores |
| `--accent` | `#f0a926` | `#996600` | destaque secundário (dourado, herança do tema antigo) |
| `--accent2` | `#3f8fc4` | `#2569a0` | destaque azul (links, info) |
| `--green` | `#3ddc70` | `#0a6b39` | positivo semântico (alta, acerto, over) |
| `--red` | `#ff5449` | `#c22a22` | negativo semântico (queda, erro, under) |
| `--yellow` | `#f59e0b` | `#b45309` | alerta/atenção |
| `--gray` | `#6b7280` | `#6b7280` | texto desabilitado/neutro |
| `--text` | `#f2f1ec` | `#1a1a1a` | texto principal |
| `--text2` | `#9aa39c` | `#5c6660` | texto secundário/meta |

Cores **fixas nos dois temas de propósito** (não usar `var()`, são objetos
físicos na metáfora visual, não superfícies do site):
- `--pitch: #0f2318` — fundo de campo/placar
- `--scoreboard: #050505` — fundo do painel de LED
- `--led: #ff5a36` — texto do painel de LED

Verde de marca (accent do redesign, uso pontual — pílulas de destaque, tag
de odds ao vivo, badge PPG): `#00e676` (texto/borda) sobre `#0c3322`
(fundo) no escuro. Ainda não virou token `--brand` formal; ao introduzir em
novo componente, considere promover para variável em vez de repetir o hex.

## Tipografia

```
--font-body:      'Segoe UI', 'IBM Plex Sans', system-ui, sans-serif   (corpo)
--font-display:    'Space Grotesk', 'IBM Plex Sans', system-ui, sans-serif  (logo, títulos)
--font-condensed:  'Oswald', 'Segoe UI', sans-serif                    (rótulos condensados)
--font-mono:       'JetBrains Mono', 'Space Mono', monospace           (números, odds, placar)
```

Números (odds, placares, percentuais) sempre em `--font-mono` com
`font-variant-numeric: tabular-nums` no body — colunas de número precisam
alinhar verticalmente.

Escala tipográfica (razão ~1.15), usar em qualquer componente novo/redesenhado
em vez de px ad-hoc:

| Token | Valor | Uso |
|---|---|---|
| `--text-xs` | 10px | legendas, rótulo de coluna |
| `--text-sm` | 11px | texto secundário, meta-dado |
| `--text-base` | 13px | corpo padrão |
| `--text-md` | 14px | nome de time, destaque em linha |
| `--text-lg` | 16px | subtítulo, placar grande |
| `--text-xl` | 20px | título de seção |
| `--text-2xl` | 26px | título de página/hero |

Nota: o arquivo tem centenas de `font-size` antigos fora dessa escala
(pré-2026-09-02) — fora de escopo retrofitar tudo de uma vez. A escala vale
para código novo, não é motivo para reescrever trechos antigos que não
estão sendo tocados.

## Componentes e regras

- **Odds**: sempre em formato de pílula (`border-radius` alto, ~12px+,
  padding horizontal generoso), nunca em texto solto sem container.
- **Sem gradiente, sem glow/box-shadow decorativo.** Bordas finas
  (`1px solid var(--border)`) e blocos de cor sólida é o padrão. Exceção:
  sombra funcional sutil em modal/dropdown para indicar elevação — não
  decorativa.
- **Ícones**: SVG inline de linha (`svg.ico`), 1em × 1em, herdam cor via
  `currentColor`. Não introduzir emoji novo como ícone funcional — usar SVG.
  Emojis antigos ainda em uso são débito de migração, não modelo a seguir.
- **Scrollbar customizada**: 14px, thumb com `var(--border)`, hover
  `var(--accent)`.
- **Seleção de texto** (`::selection`): fundo `var(--accent)`, texto
  `#051108` fixo (não `var(--text)` — precisa contraste garantido sobre o
  dourado independente do tema).
- **Painel de LED/placar**: cores fixas (`--pitch`, `--scoreboard`,
  `--led`), nunca trocam com o tema — é um objeto físico na metáfora, o
  tema claro/escuro é do "site", não do "estádio".

## Tema claro vs escuro

Toggle via atributo `data-theme="light"` em `<html>`. Todo token semântico
(`--bg*`, `--accent*`, `--green`, `--red`, `--text*`) tem par claro/escuro
definido — qualquer cor nova introduzida em um componente **precisa** do
par nos dois blocos `:root` / `html[data-theme="light"]`, senão fica
ilegível num dos dois temas (já aconteceu, ~80 correções).

## Onde isso vive

Todo o CSS está inline em `<style>` no topo de
[static/index.html](static/index.html) (linhas ~18-70 para os tokens). Não
há arquivo `.css` separado. Layout geral do projeto (rotas, abas, backend)
está documentado em [ARQUITETURA.md](ARQUITETURA.md).
