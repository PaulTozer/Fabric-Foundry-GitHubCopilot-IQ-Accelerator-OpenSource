# Documents and citations

Uploads accept one file per request, up to **10 MB** (images **4 MB**), with
bounded multipart reading and matching extension/MIME/signature validation.
Supported: PDF (up to 200 pages), DOCX (bounded ZIP directory and actual inflation), UTF-8 text,
Markdown, CSV, JSON, PNG/JPEG/GIF/WebP. Executable HTML/SVG and arbitrary binary
formats are rejected. Extracted context is bounded at **100,000 characters per
file**, and the UI applies a combined prompt budget.

Non-image uploads receive an opaque ID, owner principal, source ID and viewer URL.
File names are never disk paths. These documents remain in process memory for
30 minutes, max 16 / 64 MB per owner and 256 MB total, and expire on restart.
Removing a composer attachment with a document URL sends DELETE.
Expired/unowned documents return 404.
No uploaded content or BYOK keys are saved in localStorage.

Images follow a separate path: after validation, the upload API returns a data
URL held in browser page state and sent as an SDK blob attachment on the current
turn. Images do not receive a stored document ID/viewer URL and are not governed
by the server document store's 30-minute TTL, owner storage quotas or DELETE
route. Image data URLs/thumbnails remain in React conversation memory until
discarded or the page reloads. Model vision support is a provider prerequisite.

PDF.js renders PDF pages to canvas, without PDF scripting/eval. The viewer
has page controls, source ID/page labels and a download action. Markdown HTML
is skipped; text/CSV/JSON and extracted DOCX display as escaped text.
DOCX downloads retain the validated document parts in a normalized ZIP, rather
than renaming extracted text as a Word file or retaining unvalidated ZIP metadata.
Actual inflation is limited to 30 MB / 2,000 entries and a 200:1 ratio before
normalization; normalized storage counts against the same retention quotas.
The native dialog traps focus, closes on Escape and restores the activating
button. Fetches and PDF workers are cancelled/destroyed on close.

Non-image chat attachments include citation URLs in their untrusted context.
MCP sources should return a stable `source_id`, title, section/page and
`/api/documents/<id>?page=<positive integer>`. Local document links and non-image
upload chips open the viewer. Remote links are HTTPS-only external links, not
an SSRF proxy.

Each SDK turn uses a fresh session. Follow-up history contains the typed
questions and assistant answers, not prior attachment text preambles or image
inputs. A retained document chip is therefore not model memory: reattach
evidence needed on a subsequent turn, or retrieve it through an authorized tool.

## Operator source catalog

For live MCP sources, mount an operator-reviewed directory into the UI container
and set `IQ_DOCUMENT_CATALOG` to a JSON manifest:

```json
{
  "root": "C:\\Approved\\IQDocuments",
  "documents": [
    {"id": "delivery-guide", "sourceId": "knowledge-001",
     "file": "delivery-guide.pdf", "mime": "application/pdf"},
    {"id": "handover-notes", "file": "handover.md", "mime": "text/markdown"}
  ]
}
```

Catalog IDs are opaque/allowlisted, symlink-resolved files must stay beneath
the approved root, MIME is allowlisted and files cannot exceed 10 MB.
The catalog is shared with authenticated tenant users: publish only sources
that those users are authorized to read. Per-document tenant ACLs and remote
SharePoint fetching are not implemented; use an authenticated source adapter
and shared store before requiring those production scenarios.
No browser-supplied file path or remote fetch URL is accepted.
