# M1 Read-only Oracle Audit

The deterministic oracle test reads database paths only from `R31_REAL_REPORT_A_DB` and `R31_REAL_REPORT_B_DB`, opens each SQLite store with immutable read-only access, and computes the database hash at test time.

- Report A `report-version:8c355a5ba03f45619795813af83ac669`: 32 Comment Evidence plus 1 keyframe Evidence, for 33 frozen direct Evidence total; all 32 Comment Evidence resolve through exact `comment_id` relations. The report contains 33 comments whose own frozen audit result is risky. The Finding/Post sample is 3 membership Evidence versus 8 frozen direct Evidence.
- Report B `report-version:4e3ebeccd2ed4f0c9c9a750332d22585`: 10 Comment Evidence and 10 frozen direct Evidence total; all 10 Comment Evidence resolve through exact `comment_id` relations. The report also contains 13 comments whose own frozen audit result is risky.

These values match the canonical M2.2 oracle `expected = ((33, 32), (13, 10))`. They are deterministic test expectations only and are not hardcoded into product logic.
