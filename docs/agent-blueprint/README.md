# AI Trading Agent Blueprint — source

Regenerates `FTMO_AI_Trading_Agent_Blueprint.docx` in the repo root.

```bash
cd docs/agent-blueprint
python3 diagrams.py                      # img/*.png (matplotlib)
npm install docx@9                       # once
node build.js raw.docx
python3 shrink_empty.py raw.docx ../../FTMO_AI_Trading_Agent_Blueprint.docx
```

Layout rules learned the hard way, so it renders in Pages as well as Word:

- **One orientation for the whole document.** Pages ignores per-section orientation and
  flattens landscape sections to portrait, clipping wide tables.
- **Page breaks are section breaks.** Inline page-break paragraphs and `pageBreakBefore`
  both misbehave in Pages (blank pages; headings split from their tables).
- **`shrink_empty.py` is required.** docx-js ends every section with an empty 10pt
  paragraph; on a nearly full page Pages spills it onto a blank page.

## Briefing deck (`deck/build_deck.js`)

```bash
cd docs/agent-blueprint/deck
npm install pptxgenjs react-icons react react-dom sharp   # once
node build_deck.js ../../../FTMO_AI_Trading_Agent_Briefing.pptx
```

**Keynote drops every pptxgenjs chart** (even a default one) — they render blank. The
deck's charts are therefore drawn from native shapes, which look identical in Keynote and
PowerPoint. Don't switch them back to `addChart()` without re-testing in Keynote.
