# Thumbnail Primer — Sistema Britto / blog + YouTube

Estilo modelado a partir de 3 referências do nicho IA (BR + gringo) enviadas
pelo Felipe. Ver `thumbnail-refs/`. Formato-alvo: **16:9 (1280×720)**,
reaproveitável como capa de vídeo no YouTube. Geração via **gpt-image-2 (OpenAI)**
na skill `ai-image-creator`.

## Padrão extraído das referências

### Bloco A — estilo BR (as do próprio Felipe — `ref-01-br-felipe.jpg`)
- Rosto do Felipe à direita/centro, expressão forte (sorriso confiante, dedo
  apontando, ou surpresa). Recorte limpo com leve rim light.
- Ícones de app 3D glossy flutuando (Claude, Obsidian, ChatGPT, PIX).
- 1 seta branca desenhada à mão (curva), apontando pro elemento-chave.
- Fundo escuro com dados/dashboard, tons de **verde-limão** (marca).
- Texto curto e pesado + 1 badge de resultado/número (R$0,00, ROAS 3.90, ROI).
- Composição limpa, respira — credível, não poluído.

### Bloco B — estilo gringo (Hermes/Julian — `ref-02`, `ref-03`)
- Texto GIGANTE, 2-4 palavras, peso black, contorno grosso (stroke).
  Ex: "IT'S MASSIVE", "100x UPGRADES", "THIS IS INSANE".
- Rosto em choque/empolgação (boca aberta).
- Neon/glow saturado (roxo, amarelo, vermelho), raios, badge "FREE" vermelho.
- Dashboard com gráfico subindo (prova/resultado).
- Altíssimo contraste, energia agressiva.

## Receita Sistema Britto (fusão A + B)

Pegar a **legibilidade e energia** do gringo + a **credibilidade e marca** do BR.

| Elemento | Regra |
|---|---|
| Proporção | 16:9, 1280×720 |
| Rosto | Felipe (do face-bank), 1/3 do quadro, expressão coerente com a pauta |
| Hook visual | objeto-símbolo no lado oposto: ícone de app / celular / dashboard / dinheiro / logo |
| Headline | 2-5 palavras, fonte black, branco ou verde-limão, stroke escuro grosso |
| Badge | opcional — número/resultado ou "GRÁTIS"/"NOVO" em círculo/retângulo |
| Fundo | escuro + glow verde-limão da marca (evitar roxo/amarelo gringo puro) |
| Seta | no máx. 1, branca, à mão, opcional |
| Limpeza | legível em tamanho pequeno; sem poluição; máx. 1 foco visual |
| Paleta | verde-limão (#A3E635 / lime) + cinza metálico + preto (cores da marca) |

## Template de prompt para gpt-image-2

```
Thumbnail 16:9 estilo YouTube, alta qualidade, para o nicho de IA/automação.
PESSOA: [rosto do face-bank — descrição/expressão: ex. "homem jovem de óculos,
sorriso confiante apontando"]. Posicionado no terço [esquerdo/direito].
HOOK VISUAL no lado oposto: [objeto — ex. "ícone 3D glossy do app X" / "gráfico
de dashboard subindo" / "celular com tela de vendas"].
TEXTO GRANDE em destaque: "[2-5 PALAVRAS]" — fonte black, branca com contorno
escuro grosso, alto contraste. [opcional: badge "[NÚMERO/GRÁTIS]"].
FUNDO escuro com glow verde-limão (#A3E635) da marca, tons cinza metálico.
Composição limpa, 1 foco visual, legível em tamanho pequeno. Sem poluição.
[opcional: 1 seta branca desenhada à mão apontando para o elemento-chave].
```

Para consistência de rosto, usar `ai-image-creator -r <foto do face-bank>`
(edição com imagem de referência).

## Face-bank

Pasta: `assets/face-bank/`. Felipe vai enviar fotos de rosto dele em várias
expressões (neutro, sorrindo, surpreso, apontando, sério/confiante). Quanto
mais variado, melhor o casamento com cada tipo de pauta.

## Anti-padrões de thumbnail
- ❌ Texto longo (>5 palavras) ou fonte fina — ilegível no feed.
- ❌ Mais de 1 foco visual — polui.
- ❌ Paleta roxo/amarelo gringo pura — fugir da marca; usar verde-limão.
- ❌ Promessa que o post não cumpre (clickbait vazio) — público BR é calejado.
