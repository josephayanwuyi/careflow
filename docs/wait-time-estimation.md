# Wait-time estimation

Implementation: `backend/careflow/services/estimator.py` (pure functions, unit-tested in
`tests/test_estimator.py`). Estimates are recomputed every time a queue is read, so they always
reflect the latest queue and doctor state. Clients re-fetch when they receive a live update.

## Model

```
estimate = time until the doctor can see patients
         + remaining time of the patient currently with the doctor
         + patients ahead × typical consultation length
```

| Input | Source |
|---|---|
| Patients ahead | `waiting` entries ordered before this one (documented ordering in `queue_rules.py`) |
| Typical length | This doctor's completed consultations in the last `estimate_history_days` (60) days |
| Current consultation | Elapsed time of the `in_consultation` entry; a `called` patient counts as a full consultation |
| Doctor availability | Effective status: on break / temporarily unavailable with `expected_return_at` adds the remaining time; not yet arrived uses today's next scheduled session start |
| Queue paused | Treated as unavailable unless a return time is known |

**Typical length.** With at least `estimate_min_samples` (10) durations, the model uses the median, with the 25th–75th percentile as the range. Clear outliers are dropped first: values beyond both Q3 + 3·IQR and twice the median, such as a consultation left open. Consultations closed automatically at end of day have no duration and are never used. With fewer samples, it uses the doctor's `default_consult_minutes` with a range of 0.75×–1.5×, and the estimate is marked **provisional**.

**Output.** Always a range rounded to 5 minutes and at least 5 minutes wide, e.g. "Estimated wait: 25–40 minutes". States:

| State | Meaning |
|---|---|
| `next` | No one ahead and the doctor is free (shown as 0–5 min) |
| `estimated` | Range available |
| `called` / `being_seen` | The patient is with the doctor |
| `unavailable` | No number given: the doctor has checked out, is unavailable or paused with no return time, or hasn't arrived and has no upcoming session |
| `not_waiting` | Entry finished, cancelled or transferred |

Staff and doctors also receive `basis` (patients ahead, typical minutes, sample size, minutes until the doctor is available). Patients see a plain-language explanation.

## Assumptions and limitations

- **Consultations are sequential, one doctor per queue.** Department-wide pooled queues are not modelled.
- **No-shows ahead aren't predicted.** Patients ahead who miss their call make the estimate pessimistic, which is the safer direction.
- **Priority changes and transfers can move a patient back.** The estimate changes when that happens; it is not a promise.
- **Durations aren't split by service type or time of day.** Every visit type uses the doctor's single history.
- **The doctor's own reports drive availability.** A doctor who forgets to update their status makes estimates wrong. Staff can see reported versus verified status.
- **Hospital-wide disruptions** (power, emergencies) aren't modelled. Staff should pause the queue, which makes the estimate show as unavailable.

## Measuring accuracy

Each queue entry stores the estimate shown when the patient joined (`initial_estimate_min/max`) and the actual times (`entered_queue_at`, `called_at`). Before making any accuracy claims, compute this over pilot data:

- the share of patients called within their initial range;
- the median absolute error between the range midpoint and the actual wait.

A reporting endpoint for this is planned with the desktop reports screen (Phase 5). Machine learning should be considered only if this simple model proves inadequate on a meaningful dataset.
