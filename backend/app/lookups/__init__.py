"""Read-only provider lookups: a browser agent opens an allow-listed provider page and reports what
it sees. The result is evidence for a person, recorded in the audit trail. It never approves,
rejects or moves a claim, and any failure means "no evidence" (fail closed). Idea borrowed from
browser-use (MIT): typed output, domain allow-lists, a post-run injection flag, secrets as
placeholders. The browser library itself is an optional extra and is never needed by tests.
"""
