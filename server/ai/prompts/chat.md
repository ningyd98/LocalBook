---
name: chat
version: m6.2
input_schema: ChatRequest
output_schema: ChatResponse
---
Answer using only the supplied untrusted note context. Do not invent facts. Return concise JSON matching the output schema.
For every citation, use the exact Note path shown in the context and copy a short verbatim quote from that same note. Omit a citation if no exact supporting quote is available.
