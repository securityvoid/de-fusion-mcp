# changelog.md — docs/specs/setup/

| Version | Date | Change |
|---|---|---|
| 0.2.0 | 2026-11-12 | Initial spec: REQ-SETUP-001..014, TASK-SETUP-001..007 |
| 0.3.0 | 2026-11-12 | Dialog lists only detected clients; one-line-per-client preview (no entry text or manual snippets); numRows 8; unsupported-client note removed: `replace` renamed `update`; undetected-client reason only via `clients.plan`; preview skip/config.toml text and order, no-client text, export/token boxes: REQ-SETUP-002, -004, -005, -009, -010, -014; design §2, 2.1, 2.2, 2.4, 2.6, 4, 6; TASK-SETUP-001, -004 |
| 0.3.1 | 2026-11-12 | Design §4: drop preview 20000-char truncation and Destroy handler (V1-r2-1, V1-r2-2) |
| 0.3.2 | 2026-11-12 | Preview removed: one-row read-only `status` box (validation message or no-client line, OK disabled while non-empty); `changed since preview` renamed `changed since the dialog opened; reopen the dialog`: REQ-SETUP-002, -009; design §2.1, 4, 6; TASK-SETUP-004; no-client line and disabled OK hold in both modes; `status` shows the first failing check |
| 0.3.3 | 2026-11-12 | `status` box and validation messages removed; OK disabled on invalid input or no client: REQ-SETUP-002, -009; design §4, 6; TASK-SETUP-004 |
| 0.3.4 | 2026-11-12 | Detection = config file exists (no parent-directory rule): REQ-SETUP-004; design §2.6, 3, 6; TASK-SETUP-001, -004 reopened |
| 0.3.5 | 2026-11-12 | Intro wording: clients whose config file exists (C1-r1-2) |
| 0.3.6 | 2026-11-12 | Design §2.6: direct plan/apply on an absent client file creates it; dialog never does; §3.1 detection restatement dropped (C1-r1-1, C4-r1-1, C4-r1-2) |
