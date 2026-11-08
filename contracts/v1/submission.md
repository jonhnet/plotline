# Recoverable PDF submissions

Plotline Studios advertises this optional API with
`implemented.pdf_submission_idempotency: true` in `/api/v1/capabilities`.
It adds upload recovery to the existing PDF/intent handoff; inspection and
conversion still have their own status receipts. No step here starts plotting.

## Submit once, recover by key

Generate a random UUID for each new submission, and durably save it alongside
the exact PDF and intent **before** sending. Use a lowercase, hyphenated UUID,
for example `787e5ded-88bb-4ad9-a76f-8bf34ba99641`.

```http
POST /api/v1/documents
X-Plotline-Request: 1
Idempotency-Key: 787e5ded-88bb-4ad9-a76f-8bf34ba99641
Content-Type: multipart/form-data; boundary=...
```

The multipart fields remain `document` (PDF bytes) and optional `intent` (JSON
bytes). Both the first accepted request and an exact replay return HTTP **202**
with the same document ID and URLs. Replays can report a later inspection status.
The receipt includes:

```json
{
  "id": "787e5ded-88bb-4ad9-a76f-8bf34ba99641",
  "idempotency_key": "787e5ded-88bb-4ad9-a76f-8bf34ba99641",
  "source_sha256": "<SHA-256 of the exact PDF bytes>",
  "upload_intent_sha256": "<SHA-256 of the exact uploaded JSON bytes>",
  "status": "received",
  "status_url": "/api/v1/documents/787e5ded-88bb-4ad9-a76f-8bf34ba99641",
  "review_url": "/pdf?document=787e5ded-88bb-4ad9-a76f-8bf34ba99641"
}
```

`upload_intent_sha256` is null when the intent field was absent. Hashes bind the
exact bytes, including JSON whitespace/key order; retain or deterministically
serialize the original intent. Filenames and multipart boundaries do not affect
identity. The first filename is retained. Changing either payload under the same
key returns **409**, leaving the original untouched. New artwork gets a new key.
Do not infer document IDs from keys; follow the returned receipt and URLs.

After a lost response or client restart, read:

```http
GET /api/v1/submissions/787e5ded-88bb-4ad9-a76f-8bf34ba99641
```

- **200** returns the original document receipt and current inspection status.
  Verify both hashes and the key before continuing with its document ID.
- **404** means no committed submission was found at lookup time. Re-send the
  same payload with the **same key**. Concurrent requests are safe, including
  an earlier request still arriving after the lookup.
- **400** means the key is malformed. **5xx** or a network failure is inconclusive;
  wait and look up again. Do not replace the key to escape uncertainty.

The PDF, uploaded intent and receipt commit together through a single atomic
directory rename after fsync. Receipts remain with retained originals, with no
expiry. One Plotline Studios process owns each state directory. A restart or receipt
recovery resumes interrupted pure inspection; it does not create a conversion
or replay physical execution. Repeated uploads never select another pen draft.

Clients without a key retain the existing independent-upload behavior. Do not
retry an ambiguous older unkeyed upload by inventing a key afterward. Check
capabilities before relying on lookup/replay; an older server might ignore the
header or lack the lookup route. A pending keyed submission must not downgrade
to an unkeyed POST if that capability becomes unavailable.

## Continue the pen-aware handoff

The implementation accepts a source document up to 64 MiB and an uploaded
intent up to 32 MiB, with 64 KiB of additional multipart framing space. The
conversion/draft JSON endpoints accept the same 32 MiB intent size. Saved-recipe
requests allow an additional 64 KiB for their name and revision metadata. Layer
bindings are preserved, including maps with tens of thousands of feature layers.

Await inspection, check `intent_error`, then submit the complete authored intent
to `/api/v1/documents/{id}/draft` and follow its conversion and HPGL job receipts.
The draft endpoint does **not** yet accept this idempotency key. A lost draft
reply must still be reconciled against the selected conversion's exact source
and authored intent, preserving newer operator choices. See
[printing workflows](../../docs/printing-workflows.md).

Validation: `cargo test --locked --manifest-path controller/Cargo.toml`, then
`.venv/bin/python controller/scripts/submission-smoke.py` after rebuilding the
release controller. The HTTP fixture covers concurrent duplicates, byte conflicts,
lookup across restart and resumed inspection without IPP or device configuration.
