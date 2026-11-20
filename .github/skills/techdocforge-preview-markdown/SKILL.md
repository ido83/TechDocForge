---
name: techdocforge-preview-markdown
description: "Use when changing or debugging TechDocForge's Markdown preview, especially missing or empty paragraphs, tables, lists, bullets, images, or Mermaid diagrams."
---

# TechDocForge Markdown Preview

Use this workflow for changes to the Preview tab or `SafePreview`.

1. Trace the complete preview pipeline: generated Markdown cleanup, Mermaid materialization, Qt Markdown parsing, document styles, and resource loading. Do not assume the rendered view matches the Markdown source.
2. Keep the source content intact. Preview formatting must not discard ordinary paragraphs, table-cell values, list-item text, or diagram labels.
3. Configure `QTextDocument` default styles before calling `setMarkdown`; apply explicit readable foreground/background colors and table-cell/list styling so theme styles cannot make content invisible.
4. Keep local image loading restricted to generated preview assets. Do not allow arbitrary local or remote resource loading to work around missing images.
5. Add a Qt preview test containing a heading, normal paragraph, unordered and ordered lists, a populated table, and a Mermaid block. Assert both `toPlainText()` and generated document HTML contain the expected text and elements.
6. Run the focused preview and Markdown tests, then inspect an actual rendered preview when possible. A passing parser-only test is not sufficient to validate visual visibility.
7. Preserve existing behavior for Markdown editing and exports; preview-only transformations must not modify the editable source document.
