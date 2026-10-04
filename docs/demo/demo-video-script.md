# Demo video: script and shot list

Target length **3:30** (check the track's limit). Record the voiceover separately and lay it over the screen capture. UI button labels below are placeholders: confirm them against the build you freeze for recording.

## Before every take

```sh
sh scripts/demo_preflight.sh --reset   # fresh fixture run, faults cleared, services checked
```

- A reset revokes all sessions: sign in again afterwards.
- Use one stack only (`localhost:5173`, or `127.0.0.1:5175` for the branch stack). Two stacks on `localhost` log each other out.
- Browser: 1920x1080, 100% zoom, bookmarks bar hidden, notifications off, a clean profile with no other tabs.
- Warm up Gemini with one throwaway message first (the first call once timed out at the 6 s budget). Then reset again so the take starts clean.
- Open `/agent` in a second window, signed in as the agent, before recording.

## Shot list

| # | Time | Screen | Narration | Must be visible |
| --- | --- | --- | --- | --- |
| 1 | 0:00-0:20 | Title card, then a mock-up of a balance screen | "Customers can see their balance, but not why it changed. 'I reloaded Rs. 1,000 this morning. Why is my balance only Rs. 420?' Agents check several systems by hand, and a generic chatbot can't prove anything about money." | Product name, team, track |
| 2 | 0:20-0:35 | Architecture slide (README Figure 1) | "Resolve sits behind HUTCH's customer channels. The AI handles language only. Every number, decision and account change is made by code." | The line "AI handles language only" |
| 3 | 0:35-1:25 | **Case A.** Customer chat, signed in as demo customer A. Type the complaint above. | "The complaint becomes a structured case. Resolve reads the account records and reconciles them exactly: 1,000 minus 499, minus 60, minus 21 is 420." | The calculation, the source records, the evidence state SUFFICIENT |
| 4 | 1:25-2:00 | Case A continued: offer to stop the VAS, then confirm | "It offers one safe, allow-listed action. Nothing happens until the customer explicitly confirms, and the confirmation is bound to this case for five minutes." Pause on PENDING: "Pending is never shown as success." | The confirmation card, PENDING, then SUCCEEDED, then the **Trust Receipt** with its SHA-256 digest |
| 5 | 2:00-2:50 | **Case D.** Reset to case D customer, same complaint. Then switch to the `/agent` window. | "Here the balance doesn't tie out: a LKR 70 conflict. Resolve doesn't guess. It blocks any account change and creates a review ticket for a human, with the investigation attached." | CONFLICTING, no action offered, the ticket in the `/agent` queue with evidence, conversation and history tabs |
| 6 | 2:50-3:00 | (Optional) HubSpot ticket via "Open in HubSpot" | "Escalations land in the team's CRM." | Only if `CRM_PROVIDER=hubspot` and tested that day |
| 7 | 3:00-3:15 | One short Singlish or Sinhala message | "Customers can write in English, Sinhala or Tamil, including Singlish and Tanglish." | A reply in the customer's style; the figures stay unchanged |
| 8 | 3:15-3:30 | Voice panel (only if verified), then the closing card | "The same engine serves voice and text." Then: "All data is synthetic, and production would replace the adapters." | Links to repo and docs |

## What to say and what to avoid

Say: "synthetic sandbox," "deterministic reconciliation," "confirmation before any action," "pending is not success," "escalates to a human when evidence conflicts."

Avoid, because the repo doesn't support it:
- "Production-ready" or "integrated with HUTCH." Nothing is connected to HUTCH.
- Claims of Sinhala/Tamil quality. Those texts haven't been reviewed by a fluent speaker.
- "Voice works end to end" unless you ran a live mic, Gemini Live and Resolve call that day.
- "Refunds" or "credits." Only `DEACTIVATE_VAS`, `SEND_SETTINGS_INSTRUCTIONS` and `CREATE_REVIEW_TICKET` exist.

## Voiceover and editing

- Write the narration to the timings above (about 140 words per minute). Read it aloud once with a stopwatch before recording.
- Record each shot as a separate clip, so one retake doesn't mean redoing the whole video.
- Cut the idle time while a model reply loads. Don't cut the PENDING to SUCCEEDED transition: it's part of the point.
- Add on-screen captions for the three trust moments: "No guessing," "Customer confirms," "Pending is not success."

## Fallbacks

| If this fails | Do this |
| --- | --- |
| Gemini times out or runs out of quota | Structured buttons still work without a key. Re-record the clip after warming up, or use the buttons path. |
| Voice is flaky | Record it as its own take, or drop shot 8's voice and say nothing about it. |
| The HubSpot link fails | Drop shot 6. The `/agent` queue already shows the ticket. |
| A case is in a dirty state | Run `sh scripts/demo_preflight.sh --reset`, sign in again and retake the clip. |
| You want to show resilience | `python scripts/demo_faults.py arm crm-outage` makes the next ticket delivery fail once and recover. Do this only as a deliberate bonus shot. |

## Still to do (outside this file)

- Fill the README placeholders: demo video link, release tag/commit, university/batch.
- Decide which branch to freeze for the recording, and don't merge anything into it until the video is exported.
