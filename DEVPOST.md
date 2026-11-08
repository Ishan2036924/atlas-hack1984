# Devpost write-up: Atlas

**Tagline:** Say one sentence to your phone. An agent does the work, never sees your private details, and nothing happens without your yes.

**Try it:** sign up at https://app.atlasiw.com with your email (open to anyone on Nov 8). Payments run in Stripe test mode, so please don't enter a real card.

## Inspiration

AI agents can act now, not only chat. But I would not let one loose on my own inbox, calendar and card. Three things stop me: I do not know what the AI actually saw, I do not know what it will do until it has done it, and I cannot check afterward. I built Atlas as my own workspace, and this weekend I made trust the point: what the AI sees, what it does, and what proof is left.

## What it does

Atlas is a personal multi-agent AI workspace (tasks, calendar, mail, docs, sheets, boards, chat, agents). It existed before HACK1984. This weekend I added or extended, on top of it:

- **Phone Agent mode** (extended this weekend; a first version existed at the start). Talk or type one message. The agent plans in a few rows. Before anything is sent, one card shows the real values (who, what, when). You tap yes or say "send it". Then Done, with Undo where the action allows it. If a long request hits a limit, you tap Keep going.
- **Privacy masking with signed receipts.** Every request Atlas sends to an AI model is masked with my open-source Open Pseudonym: names, emails, phones and secrets become stand-ins. The request is checked before it leaves (if a real value is still in it, it does not go). Each request gets a signed, hash-chained receipt. Settings has "What the AI saw", so you read the masked text next to your own.
- **Payments in Stripe test mode.** The owner can ask Atlas to pay a named payee an exact amount. It needs a tap on a card, has spend limits, and one approval can charge at most once. There is a Payments page with a timeline, refunds and limits. Test mode only: Stripe's test card, no real money.
- **A natural voice from Gradium.** Atlas asks its questions aloud and reads answers in a Gradium voice, with OpenAI as the fallback. You pick Natural, Calm or Warm.
- **A learning loop.** Signals from what you accept or decline, a judge that uses no model, and a guard so it cannot loosen its own rules.

## How we built it

React and TypeScript on the front end (works on a phone and a laptop). Python 3.12 with FastAPI and FastMCP tools on the back end. Supabase Postgres with row-level security. It runs on an Oracle Cloud Always Free VM. Models are OpenAI's. Voice is Gradium. Payments are Stripe test mode.

The safety rules are enforced in code, not in prompts. Every tool has a permission tier. Sends and payments are tier C: they need your tap, and the server checks a signed approval that covers the exact arguments. The model can make a rule stricter but never looser. Masking runs at the one place where requests leave for the model, and it fails closed. My two open-source kits (Open Pseudonym and Open Action Consent, both Apache-2.0) carry the privacy and consent ideas.

The weekend was 153 commits on top of a working app. The repo link holds the weekend's code and the full weekend diff; the full app is private because it is a production multi-user app.

## Challenges

- Making the phone feel calm: one request, rows that settle, one card, no noise. The animation timing took many small fixes.
- Masking without making the AI worse. Dates, links and money must stay real or the tools break; names and contact details can be stand-ins. Getting the stand-ins to restore correctly, even when a model streams them in pieces, was the hard part.
- Payments safely: no double charge when Stripe is slow, a lock that stops everything if a live-mode answer ever appears, and a limit that is checked before Stripe is called.
- Voice that does not get stuck on iPhone, and a microphone that keeps listening until you stop it.

## Accomplishments

- A real flow on a phone: speak, see the plan, approve with the real values, Done, Undo.
- Every model request leaves a signed receipt, and you can read what the AI saw.
- Payments that are approval-gated, limited and refundable, in test mode.
- A Gradium voice working with an automatic fallback.
- Honest accounting: all footage is the real app on sample data, and the repo lists exactly what is weekend work.

## What we learned

- Trust is the blocker for agents, not capability. People need to see the values, say yes, and keep a receipt.
- The best privacy feature is one you can read. "What the AI saw" is more convincing than a promise.
- Put the rules in code. A prompt that says "never pay without asking" is not a control.

## What's next

- Bring more people in after judging.
- Browser actions and real payments only after a proper security review (today: test mode, owner only).
- Let others verify a receipt without trusting Atlas, using the open Open Action Consent and Open Pseudonym verifiers.
- A local-model option for the most private tasks.

## Real versus mocked (read this)

- All video and screenshots are the real app on sample data.
- Payments are Stripe test mode. No real money moves.
- A dinner is held on the calendar. Nothing is booked with a restaurant.
- Atlas existed before the hackathon (state at Sat 11:00 AM ET: commit 9b662cd2). The weekend range is 9b662cd2..16a25c0b. A first version of the phone face was already in that base commit; the weekend extended it.

## Built with

React, TypeScript, Python, FastAPI, FastMCP, Supabase (Postgres), Oracle Cloud, OpenAI API, Gradium, Stripe (test mode), Open Pseudonym, Open Action Consent, Tailscale
