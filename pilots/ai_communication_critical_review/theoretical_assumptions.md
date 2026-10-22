<!-- origin: SCRIPT_FROM_STORE using the suite's document renderer; this file reports that the content is not available, it contains no analysis -->
# Theoretical assumptions
**NOT_AVAILABLE: No full text of any included study is held in the project (every Source is METADATA_ONLY). The researcher must supply lawfully accessible full texts.; No approved model run: the landscape needs the reading protocols run by a model, which sends text to a provider; this needs the researcher's permission per source, a budget the suite can account for, and a rotated API key.; Backlog B1 (partial provider usage) is still open: a live run could not be metered reliably enough for an uncontrolled batch.; The researcher has not adopted the review protocol or screened any record.**


This file would contain the assumptions each study states, those it says it tests, and any assumption a reader or the suite proposes as implied (kept apart from what authors wrote), each with its quote and place.

## Why it is not available

* No full text of any included study is held in the project (every Source is METADATA_ONLY). The researcher must supply lawfully accessible full texts.
* No approved model run: the landscape needs the reading protocols run by a model, which sends text to a provider; this needs the researcher's permission per source, a budget the suite can account for, and a rotated API key.
* Backlog B1 (partial provider usage) is still open: a live run could not be metered reliably enough for an uncontrolled batch.
* The researcher has not adopted the review protocol or screened any record.

## What would produce it

1. Full texts lawfully held and imported (`research corpus import`), screened and included by the researcher (`research review-protocol screen`, then `adopt`).
2. `research landscape build RET_x` with an approved model and budget; then `landscape propose`, the researcher's review, `landscape candidates` and `landscape inspect`.
3. `research export landscape LAND_x`, which writes the conceptual map, assumptions, relations and inspections from the store.

Status: REAL_CORPUS_VALIDATION_PENDING. No synthetic result is substituted for it.
