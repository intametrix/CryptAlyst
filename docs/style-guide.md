# CryptAlyst document style guide — "format like this"

This is the official look, taken from the **Design & Architecture Reference**
(`docs/CryptAlyst-Design-and-Architecture-Reference.docx`). Apply it to any
CryptAlyst document or diagram.

## 1. Document typography

| Element | Font | Size | Colour |
|---|---|---|---|
| Body | Calibri (PDF: Helvetica) | 10 pt | near-black `#1a1a1a`, line spacing 1.1, 5 pt after |
| Heading 1 / chapter | Calibri bold | 15.5 pt | accent `#1F4E79` |
| Heading 2 | Calibri bold | 12 pt | accent `#1F4E79` |
| Heading 3 | Calibri bold | 10.5 pt | muted `#596370` |
| Caption | Calibri italic | 8 pt | muted `#596370`, centred |
| Mono / code | Courier New (PDF: Courier) | 8 pt | on `#F1F3F6` fill, left-indent 0.22″ |
| Table body | Calibri | 8.3 pt | — |
| Table header | Calibri bold | 8.6 pt | white on accent |

Colours: **accent** `#1F4E79`, **muted** `#596370`, **mono fill** `#F1F3F6`.

## 2. Page setup

A4 (21.0 × 29.7 cm). Margins: left/right **0.8″**, top/bottom **0.75″**.
Content width ≈ 6.67″. Page numbers in the footer (not on the cover).

## 3. Tables

Use the Word style **"Light Grid Accent 1"**: thin grid, header row filled
accent blue with white bold text, body 8.3 pt, column widths set in inches.
In the PDF builder the header is `BACKGROUND #1F4E79` + `TEXTCOLOR #ffffff`.

## 4. Figures

Fit to the content width (≤ 6.67″) and a max height of 7.9″, centred, with an
italic 8 pt muted caption underneath ("Figure N.M — …").

## 5. Mermaid diagrams

No `%%{init}` theme. Colour **per node by role** with these seven `classDef`s
(pastel fill + dark stroke + dark text, 2 px stroke):

```
classDef c_market  fill:#dbe9fb,stroke:#1d4e89,color:#0b2545,stroke-width:2px;
classDef c_intel   fill:#d9f2e3,stroke:#1d7a4d,color:#0d3b26,stroke-width:2px;
classDef c_model   fill:#ffe9c7,stroke:#b7791f,color:#4a2f00,stroke-width:2px;
classDef c_risk    fill:#fbdcdc,stroke:#a4232b,color:#4a0f13,stroke-width:2px;
classDef c_control fill:#d8eef2,stroke:#0f6b7a,color:#08343c,stroke-width:2px;
classDef c_store   fill:#e9e3f7,stroke:#5b3fa8,color:#2a1a55,stroke-width:2px;
classDef c_neutral fill:#eef1f5,stroke:#4b5b6e,color:#1f2b38,stroke-width:2px;
```

Tag every node `class NodeId c_role`; group with `subgraph`. Roles:
raw feed → `c_market`; TA/forecast/signals/detectors → `c_intel`;
models/engines/books → `c_model`; risk/refusals → `c_risk`;
supervisor/control → `c_control`; recorder/lake/stores → `c_store`;
RabbitMQ/envelopes/other → `c_neutral`.

Render: `mmdc -i d.mmd -o d.png -b white -s 3`
(with `PUPPETEER_EXECUTABLE_PATH` pointing at a Chrome binary).
