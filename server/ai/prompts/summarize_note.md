---
name: summarize_note
version: m6.1
input_schema: SummarizeRequest
output_schema: SummarizeResponse
---
Summarize only the supplied Markdown evidence. Do not infer facts not stated in the input. Preserve uncertainty and explicitly state when the supplied evidence is insufficient. The caller may provide one line-labelled section or a collection of extracted facts; include original line markers (for example [L12-L18]) in key_points whenever line labels are present. Return concise JSON with a summary and key points.
