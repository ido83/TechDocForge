---
name: techdocforge-mermaid-diagrams
description: "Use when creating or improving TechDocForge Mermaid diagrams, choosing workflow-specific layouts and colors, or changing PNG rendering, preview, or export."
---

# Professional Mermaid Diagrams

Use this workflow when changing Mermaid prompts, rendering, preview, or export.

1. Keep diagrams focused on one purpose. Include only source-supported components, actors, steps, and relationships; do not infer missing details.
2. Match the diagram type, direction, and visual emphasis to what readers need to follow. Use these patterns as starting points.

   | Workflow | Diagram and layout | Color emphasis |
   | --- | --- | --- |
   | Linear process or delivery pipeline | Left-to-right flowchart on a wide page. Use top-to-bottom for a narrow page or long labels. | Neutral steps with a blue or teal accent on significant stages or handoffs. |
   | Approval, branching, or escalation | Top-to-bottom flowchart with decision diamonds and explicit branch labels where supported. | Amber for decisions or pending review, green for approved outcomes, and red for rejection or failure. |
   | Architecture or data flow | Flowchart arranged from inputs through systems to outputs. Group related components by layer or ownership when the renderer supports it. | Muted blue for internal systems, slate for external dependencies, and teal for data stores when those distinctions matter. |
   | API calls or cross-service handoffs | Sequence diagram with participants ordered by their role in the interaction and messages read from top to bottom. | Muted participant colors to distinguish roles where supported. Reserve amber or red for alternate or error paths that readers need to notice. |

3. Keep styling consistent across related diagrams. Reuse the same color for the same meaning and adapt the palette to the document's existing style. Pair color with text labels, shape, or grouping so the diagram also works in grayscale. Use short labels, strong text contrast, enough spacing, and clear connections. Wrap long labels and reduce crossing edges.
4. Check what the target renderer supports before choosing styling syntax. TechDocForge's built-in PNG renderer uses a fixed palette and does not apply Mermaid classes or themes. Where color styling is unavailable, communicate the same distinctions through labels and layout. If the task requires color in PNG output, verify that the selected rendering path actually applies it.
5. In sequence diagrams, keep message labels separate from arrows and lifelines; allocate row height for wrapped messages.
6. Render to PNG using the supported local renderer. Preserve the Mermaid source in the editable Markdown; apply preview-only rendering without altering the source.
7. Verify the actual rendered image, not just parsed Markdown. Check that labels are legible, nodes and edges do not overlap, the entire image is visible, and the diagram still makes sense at preview and document-export sizes.
8. Add or update tests for representative diagrams, including long labels, branches, multiple incoming/outgoing edges, and image materialization. Run focused preview/export tests and inspect a rendered sample.
