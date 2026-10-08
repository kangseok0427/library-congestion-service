# API contract v2

ADE-43 freezes the interface that the administrator and visitor web pages use.
Frontend work must not wait for the server implementation: develop against the
JSON files in `fixtures/`, then replace only the transport adapter when the
FastAPI endpoints are ready.

## Rules

- Visitor results use all prior years with the same month/day/hour and valid
  cumulative observations. They are historical statistics, not future forecasts.
  No four-week cutoff or other-date fallback applies. `historical_statistics`
  responses include per-hour `sample_count`, `source_dates`, `source_years` and
  top-level `statistics`. Legacy field names remain for compatibility;
  `expected_visitors` is the observed IN mean and bars use `estimated_present`.
  `matched_source_dates` and `excluded_samples` distinguish absent dates from
  matched dates whose cumulative values cannot be calculated. Top-level
  `statistics.matched_dates` counts original matching dates, while `source_dates`
  lists dates actually used. Do not infer that a discarded hour was empty.
  Recommendations require complete coverage of the hours being compared; a
  surviving morning fragment must not become a claim that mornings are quieter.
  Negative computed balances are corrected with `max(0, previous + IN - OUT)`
  after summing both gates. Corrected zero values remain in the average and the
  next hour continues from zero. Raw IN/OUT are preserved. `corrected_source_dates`
  and `corrected_sample_count` identify the included corrected observations.
  `GET /api/v1/meta` adds `date_availability.available_dates` / `skipped_dates`
  for the existing today-to-7-days range. The visitor page skips open dates with
  no same-calendar-date prior-year source and updates the selected date and hint.
  Closure notices remain selectable. Date-specific API responses retain the
  explicitly requested date; they never silently return a different date.

- Base path: `/api/v1`
- JSON field names: `snake_case`
- Calendar dates: `YYYY-MM-DD`
- Timestamps: ISO 8601 with the KST offset (`+09:00`)
- Only Excel upload uses `multipart/form-data`; every other request/response is JSON.
- All errors use `{ "error": { "code": "...", "message": "...", "details": [] } }`.
- The browser displays the backend's `level` and values; it does not reproduce the
  congestion calculation.
- Admin authentication uses a secure HttpOnly session cookie. The replacement
  admin page must not expose a token input.

`openapi-v2.yaml` is the source of truth. If prose, a fixture, and the OpenAPI
file disagree, update them together in one pull request and obtain ADE-43 owner
approval before merging.

## Mock-first frontend mapping

| UI state | Fixture |
| --- | --- |
| Login success | `fixtures/admin-session-success.json` |
| Login failure | `fixtures/admin-session-invalid.json` |
| Excel publish success | `fixtures/admin-upload-success.json` |
| Excel validation failure | `fixtures/admin-upload-invalid.json` |
| Four retained versions | `fixtures/admin-versions-four.json` |
| Rollback success | `fixtures/admin-rollback-success.json` |
| Open-day bars and message | `fixtures/congestion-open.json` |
| Closed day | `fixtures/congestion-closed.json` |
| Insufficient data | `fixtures/congestion-insufficient.json` |

The frontend adapter may return these objects directly in mock mode. Production
mode must call the same paths and return the same object shapes.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/admin/session` | Sign in and set the admin session cookie |
| `GET` | `/api/v1/admin/session` | Read the current authentication state |
| `DELETE` | `/api/v1/admin/session` | Sign out |
| `POST` | `/api/v1/admin/uploads` | Validate and publish one `.xlsx` file (max 10 MB) |
| `GET` | `/api/v1/admin/versions` | List the newest four JSON versions |
| `POST` | `/api/v1/admin/versions/{version_id}/rollback` | Activate a retained version |
| `GET` | `/api/v1/congestion/today?date=YYYY-MM-DD` | Get bars and the visitor guidance message |

## Ownership boundaries

- ADE-44 owns the Excel validity rules and error examples.
- ADE-45 owns preprocessing, atomic publish, four-version rotation, and rollback.
- ADE-46 owns the FastAPI session and admin endpoints.
- ADE-47 and ADE-49 own the web pages and start from these fixtures.
- No task may create a second incompatible schema inside its own module.

