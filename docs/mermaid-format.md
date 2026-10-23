# CryptAlyst Mermaid format — "format like this"

This is the diagram style used in the **Design & Architecture Reference**
(`docs/CryptAlyst-Design-and-Architecture-Reference.docx` / `.pdf`).
To say "format like this", apply the rules below to any new Mermaid diagram.

## 1. No `%%{init}` theme directive

Do NOT set a `theme` or `themeVariables`. Use Mermaid's default theme
(white background, default sans-serif font) and colour **per node by role**
with `classDef` instead. Node labels use `<br/>` for line breaks and quoted
strings `["..."]` for multi-word labels.

## 2. The role palette (exactly these seven `classDef`s)

Append this block at the end of every diagram (after a `%% colour by role`
comment):

```
%% colour by role
classDef c_market  fill:#dbe9fb,stroke:#1d4e89,color:#0b2545,stroke-width:2px;
classDef c_intel   fill:#d9f2e3,stroke:#1d7a4d,color:#0d3b26,stroke-width:2px;
classDef c_model   fill:#ffe9c7,stroke:#b7791f,color:#4a2f00,stroke-width:2px;
classDef c_risk    fill:#fbdcdc,stroke:#a4232b,color:#4a0f13,stroke-width:2px;
classDef c_control fill:#d8eef2,stroke:#0f6b7a,color:#08343c,stroke-width:2px;
classDef c_store   fill:#e9e3f7,stroke:#5b3fa8,color:#2a1a55,stroke-width:2px;
classDef c_neutral fill:#eef1f5,stroke:#4b5b6e,color:#1f2b38,stroke-width:2px;
```

Palette (pastel fill + dark stroke + dark text, 2px stroke):

| Role | class | fill | stroke | text |
|---|---|---|---|---|
| Market data (raw feed) | `c_market` | `#dbe9fb` | `#1d4e89` | `#0b2545` |
| Intelligence (derived: TA, forecast, signals) | `c_intel` | `#d9f2e3` | `#1d7a4d` | `#0d3b26` |
| Model / decision | `c_model` | `#ffe9c7` | `#b7791f` | `#4a2f00` |
| Risk / refusal | `c_risk` | `#fbdcdc` | `#a4232b` | `#4a0f13` |
| Control / orchestration | `c_control` | `#d8eef2` | `#0f6b7a` | `#08343c` |
| Store / persistence | `c_store` | `#e9e3f7` | `#5b3fa8` | `#2a1a55` |
| Neutral / other | `c_neutral` | `#eef1f5` | `#4b5b6e` | `#1f2b38` |

## 3. Assign each node to a role

After the `classDef`s, tag every node: `class NodeId1,NodeId2 c_role`.
Group related nodes with `subgraph` and colour the members by role.
Roles map to the platform planes:

- raw Binance feed → `c_market`
- tad / forecast / signals / feeds / detectors → `c_intel`
- models / engines / books → `c_model`
- risk governor / refusals → `c_risk`
- supervisor / orchestrator / control → `c_control`
- recorder / lake / stores → `c_store`
- RabbitMQ / envelopes / everything else → `c_neutral`

## 4. Diagram types

Use `flowchart TD` (or `TB`/`LR`), `sequenceDiagram`, `stateDiagram-v2`,
or `graph TB`/`LR` — whichever fits the flow. Sequence diagrams need no
colour classes (participants are styled by the default theme).

## 5. Render

Render with `mmdc` (mermaid-cli) to PNG, white background, scale 3 for print:

```
PUPPETEER_EXECUTABLE_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  mmdc -i diagram.mmd -o diagram.png -b white -s 3
```
