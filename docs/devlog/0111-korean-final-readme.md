# 0111 — Final Korean-first README

Date: 2026-09-21. Scope: documentation only; no cluster or AWS resources created.

## Why

The repository default README was English while the existing Korean translation
described several generic-change features as unfinished and cited old test counts.
That made the entry point inconsistent with current code and experiment records.

## What and how

`README.md` is now the Korean default. `README.en.md` preserves the complete English
reference with bidirectional navigation. The old `README.ko.md` is now a short link
to the default page, avoiding broken historical links and a drifting duplicate.
The default page emphasizes the decision flow,
one-command demo, current evidence, and safety limits; the English reference keeps
the detailed CLI workflows. Historical devlog entries 0073 and 0074 are not rewritten.

The status wording separates a prior PASS Pair and Draft PR from later EKS read-only
analysis and a local throttling-gate FAIL. Request-cost projection is not called an
AWS bill saving. Synthetic load is not called real-user traffic.

## Evidence and decision

Both language links and local Markdown targets were checked after the move.
Documentation-only changes do not alter runtime behavior; no new benchmark or AWS
claim is introduced. The current repository remains publishable as an open-source
artifact, while production savings and real-user validation remain unproven.
